"""Offline tests for the supplemental intake tool; never touch providers or Docker."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import semantic_quality_intake as s


def fixture():
    return {
        'schema_version': 1, 'dataset_id': 'public_eval',
        'source_type': 'public', 'annotation_protocol_id': 'blind_review_v1',
        'cases': [
            {'case_id':'case_one','domain':'policy','change_category':'deadline',
             'rights_basis':'public_reviewed','source_reference':'public_record_1',
             'old_text':'<script>alert(1)</script> Refund: 30 days.',
             'new_text':'Refund: 3 days.'},
            {'case_id':'case_two','domain':'policy','change_category':'paraphrase',
             'rights_basis':'public_reviewed','source_reference':'public_record_2',
             'old_text':'Export within 7 days.','new_text':'Data export occurs within seven days.'},
        ],
    }


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sde-p12-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.intake = self.root/'intake.json'
        self.intake.write_bytes(s.json_bytes(fixture()))
        self.directory = self.root/'reviews'

    def annotate(self, first='material', second='equivalent'):
        for reviewer in ('a','b'):
            path = self.directory/f'reviewer_{reviewer}.csv'
            rows = s.read_csv(path, ['case_id','label'])
            for row in rows:
                row['label'] = first if row['case_id'] == 'case_one' else second
            s.write_csv(path,['case_id','label'],rows)

    def test_prepare_blinded_html_escaped_and_csv_has_no_text(self):
        s.prepare(self.intake,self.directory)
        html = (self.directory/'reviewer_a.html').read_text('utf-8')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        rows = s.read_csv(self.directory/'reviewer_a.csv', ['case_id','label'])
        self.assertEqual({k for k in rows[0]}, {'case_id','label'})
        self.assertEqual({r['label'] for r in rows}, {''})

    def test_finalized_is_compatible_with_production_holdout_loader(self):
        s.prepare(self.intake,self.directory)
        self.annotate()
        result=self.root/'holdout.json'
        s.finalize(self.intake,self.directory,result,attest_frozen=False)
        from src.services.semantic_holdout_evaluation import load_holdout, plan_holdout
        cases, digest, metadata = load_holdout(result)
        self.assertEqual(len(cases),2)
        self.assertEqual(metadata['holdout_declared'],False)
        self.assertEqual(plan_holdout(cases,digest,metadata)['provider_requests'],0)
        receipt = json.loads((self.root/'holdout_receipt.json').read_text('utf-8'))
        self.assertEqual(receipt['corpus_sha256'],s.hash_bytes(result.read_bytes()))

    def test_disagreement_requires_adjudication(self):
        s.prepare(self.intake,self.directory)
        self.annotate()
        rows = s.read_csv(self.directory/'reviewer_b.csv', ['case_id','label'])
        for row in rows:
            if row['case_id']=='case_one': row['label']='equivalent'
        s.write_csv(self.directory/'reviewer_b.csv',['case_id','label'],rows)
        result=self.root/'holdout.json'
        with self.assertRaisesRegex(ValueError,'adjudication'):
            s.finalize(self.intake,self.directory,result,attest_frozen=False)
        self.assertFalse(result.exists())
        s.write_csv(self.directory/'adjudication.csv',['case_id','final_label','reason_code'],
                    [{'case_id':'case_one','final_label':'material','reason_code':'time'}])
        s.finalize(self.intake,self.directory,result,attest_frozen=False)
        receipt=json.loads((self.root/'holdout_receipt.json').read_text('utf-8'))
        self.assertEqual(receipt['adjudication_count'],1)

    def test_suspicious_labels_rejected(self):
        s.prepare(self.intake,self.directory)
        self.annotate()
        rows=s.read_csv(self.directory/'reviewer_b.csv',['case_id','label'])
        rows[0]['label']='unknown'
        s.write_csv(self.directory/'reviewer_b.csv',['case_id','label'],rows)
        with self.assertRaisesRegex(ValueError,'label'):
            s.finalize(self.intake,self.directory,self.root/'final.json',attest_frozen=True)

    def test_tampered_intake_fails_sha_check(self):
        s.prepare(self.intake,self.directory)
        self.annotate()
        changed=fixture(); changed['cases'][0]['old_text']='Changed later'
        self.intake.write_bytes(s.json_bytes(changed))
        with self.assertRaisesRegex(ValueError,'changed'):
            s.finalize(self.intake,self.directory,self.root/'final.json',attest_frozen=False)

    def test_duplicate_pairs_rejected(self):
        value=fixture()
        value['cases'][1]['old_text']=value['cases'][0]['new_text']
        value['cases'][1]['new_text']=value['cases'][0]['old_text']
        with self.assertRaisesRegex(ValueError,'duplicate'):
            s.validate_intake(value)

    def test_double_json_keys_rejected(self):
        self.intake.write_text('{"schema_version":1,"schema_version":1}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'repeated'):
            s.safe_json_file(self.intake)

    def test_private_paths_inside_repo_are_rejected(self):
        with patch.object(s,'repo_root',return_value=self.root):
            with self.assertRaisesRegex(ValueError,'outside'):
                s.prepare(self.intake,self.directory)

    def test_self_test_stays_offline(self):
        s.self_test()


if __name__ == '__main__':
    unittest.main(verbosity=2)

class SoloProtocolTests(unittest.TestCase):
    """Single founder labels do not acquire fictional reviewer independence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sde-p27-solo-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.intake = self.root / 'intake.json'
        self.intake.write_bytes(s.json_bytes(fixture()))
        self.directory = self.root / 'reviews'
        s.prepare(self.intake, self.directory, reviewers=1)

    def mark(self, one='material', two='equivalent'):
        rows=s.read_csv(self.directory / 'reviewer_a.csv', ['case_id','label'])
        for row in rows:
            row['label'] = one if row['case_id']=='case_one' else two
        s.write_csv(self.directory / 'reviewer_a.csv', ['case_id','label'],rows)

    def test_single_reviewer_packet_escaped_no_second_reviewer(self):
        self.assertFalse((self.directory / 'reviewer_b.csv').exists())
        self.assertFalse((self.directory / 'adjudication.csv').exists())
        packet=(self.directory / 'reviewer_a.html').read_text('utf-8')
        self.assertIn('ONE-person diagnostic', packet)
        self.assertIn('&lt;script&gt;', packet)
        self.assertNotIn('<script>',packet)
        manifest=json.loads((self.directory/'manifest.json').read_text('utf-8'))
        self.assertEqual(manifest['reviewer_count'],1)
        self.assertFalse(manifest['annotation_independence_verified'])

    def test_single_reviewer_finalization_is_explicitly_non_independent(self):
        self.mark()
        path=self.root/'solo.json'
        s.finalize(self.intake, self.directory, path, attest_frozen=False)
        from src.services.semantic_holdout_evaluation import load_holdout, plan_holdout
        cases,digest,metadata=load_holdout(path)
        self.assertEqual(len(cases),2)
        self.assertEqual(metadata['annotator_count'],1)
        self.assertFalse(metadata['adjudicated_declared'])
        self.assertFalse(metadata['holdout_declared'])
        receipt=json.loads((self.root/'solo_receipt.json').read_text('utf-8'))
        self.assertEqual(receipt['reviewer_count'],1)
        self.assertEqual(receipt['evaluation_scope'],'single_owner_diagnostic_only')
        self.assertIsNone(receipt['reviewer_b_sha256'])
        self.assertIsNone(receipt['adjudication_sha256'])
        self.assertEqual(receipt['corpus_sha256'],s.hash_bytes(path.read_bytes()))
        self.assertEqual(plan_holdout(cases,digest,metadata)['provider_requests'],0)

    def test_single_reviewer_forbids_independent_holdout_attestation(self):
        self.mark()
        result=self.root/'solo.json'
        with self.assertRaisesRegex(ValueError,'cannot attest'):
            s.finalize(self.intake,self.directory,result,attest_frozen=True)
        self.assertFalse(result.exists())

    def test_single_reviewer_refuses_guessing_when_uncertain(self):
        self.mark(one='unsure')
        result=self.root/'solo.json'
        with self.assertRaisesRegex(ValueError, 'certain material'):
            s.finalize(self.intake,self.directory,result,attest_frozen=False)
        self.assertFalse(result.exists())

    def test_single_reviewer_reports_excluded_unsure_in_larger_sample(self):
        data=fixture()
        data['cases'].append(dict(data['cases'][0], case_id='case_three',
                                 old_text='Incident response must take 5 hours.',
                                 new_text='Incident response must take 1 hour.'))
        self.intake.write_bytes(s.json_bytes(data))
        # Start new review dir to preserve initial intake identity.
        other=self.root/'other'
        s.prepare(self.intake,other,reviewers=1)
        rows=s.read_csv(other/'reviewer_a.csv',['case_id','label'])
        labels={'case_one':'material','case_two':'equivalent','case_three':'unsure'}
        for row in rows: row['label']=labels[row['case_id']]
        s.write_csv(other/'reviewer_a.csv',['case_id','label'],rows)
        out=self.root/'solo.json'
        s.finalize(self.intake,other,out,attest_frozen=False)
        receipt=json.loads((self.root/'solo_receipt.json').read_text('utf-8'))
        corpus=json.loads(out.read_text('utf-8'))
        self.assertEqual(len(corpus['cases']),2)
        self.assertEqual(receipt['excluded_unsure_count'],1)
        self.assertEqual(receipt['reviewer_count'],1)

    def test_manifest_mode_or_intake_tampering_blocks(self):
        self.mark()
        path=self.directory/'manifest.json'
        raw=json.loads(path.read_text('utf-8'))
        raw['reviewer_count']=2
        path.write_bytes(s.json_bytes(raw))
        with self.assertRaises((ValueError, FileNotFoundError)):
            s.finalize(self.intake,self.directory,self.root/'solo.json',attest_frozen=False)
        self.assertFalse((self.root/'solo.json').exists())

    def test_reviewer_count_type_validation(self):
        for value in (0,3,True,'1'):
            with self.assertRaises(ValueError):
                s.prepare(self.intake,self.root/'invalid',reviewers=value)

    def test_output_collision_preserves_existing_receipt(self):
        self.mark()
        preexisting=self.root/'solo_receipt.json'
        preexisting.write_text('preserve me',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'already exists'):
            s.finalize(self.intake,self.directory,self.root/'solo.json',attest_frozen=False)
        self.assertEqual(preexisting.read_text('utf-8'),'preserve me')
        self.assertFalse((self.root/'solo.json').exists())
