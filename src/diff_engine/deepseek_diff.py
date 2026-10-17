"""
Semantic diff engine using DeepSeek API.

The engine is intentionally strict about analysis failures. A failed
DeepSeek request or invalid model response must never be represented as
a successful semantic analysis with an ``unknown`` risk level.

This protects downstream drift events from false confidence.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

from openai import OpenAI

sys.path.append(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__)
            )
        )
    )
)

from src.core.config import settings


logger = logging.getLogger(__name__)


class DeepSeekSemanticDiffError(Exception):
    """
    Raised when semantic analysis cannot be completed reliably.

    Callers should treat this as an analysis failure rather than as a
    semantic result. In particular, this must not be converted into a
    normal drift event with an ``unknown`` risk score.
    """

    pass


class DeepSeekPermanentError(DeepSeekSemanticDiffError):
    """Input/configuration failure that will not recover by repeating the call."""


class DeepSeekTransientError(DeepSeekSemanticDiffError):
    """Provider/network failure eligible for a bounded retry."""


class DeepSeekSemanticDiffEngine:
    """
    Engine for detecting semantic changes using the DeepSeek API.

    Configuration is read from application settings so production
    deployments can change the model and request limits without modifying
    application code.
    """

    DEFAULT_MODEL = "deepseek-v4-flash"
    DEFAULT_MAX_INPUT_CHARS = 30000
    DEFAULT_TIMEOUT_SECONDS = 60.0

    ALLOWED_CATEGORIES = {
        "pricing",
        "legal",
        "policy",
        "feature",
        "other",
    }

    ALLOWED_RISK_LEVELS = {
        "high",
        "medium",
        "low",
    }

    def __init__(
        self,
        model: Optional[str] = None,
        max_input_chars: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ):
        """
        Initialize the DeepSeek semantic diff engine.

        Args:
            model:
                DeepSeek model name. Defaults to configured model.
            max_input_chars:
                Maximum characters sent for each document side.
            timeout_seconds:
                HTTP request timeout for the DeepSeek API.

        Raises:
            ValueError:
                If the API key is missing or configuration is invalid.
        """
        api_key = settings.DEEPSEEK_API_KEY

        if not api_key:
            raise DeepSeekPermanentError(
                "DEEPSEEK_API_KEY not set in environment"
            )

        configured_model = getattr(
            settings,
            "DEEPSEEK_MODEL",
            self.DEFAULT_MODEL,
        )

        configured_max_input_chars = getattr(
            settings,
            "DEEPSEEK_MAX_INPUT_CHARS",
            self.DEFAULT_MAX_INPUT_CHARS,
        )

        configured_timeout_seconds = getattr(
            settings,
            "DEEPSEEK_TIMEOUT_SECONDS",
            self.DEFAULT_TIMEOUT_SECONDS,
        )

        self.model = (
            model
            or configured_model
            or self.DEFAULT_MODEL
        )

        self.max_input_chars = (
            max_input_chars
            if max_input_chars is not None
            else configured_max_input_chars
        )

        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else configured_timeout_seconds
        )

        if self.max_input_chars <= 0:
            raise DeepSeekPermanentError(
                "DEEPSEEK_MAX_INPUT_CHARS must be greater than 0"
            )

        if self.timeout_seconds <= 0:
            raise DeepSeekPermanentError(
                "DEEPSEEK_TIMEOUT_SECONDS must be greater than 0"
            )

        try:
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com/v1",
                timeout=self.timeout_seconds,
                max_retries=0,
            )
        except Exception as exc:
            raise DeepSeekPermanentError(
                "DeepSeek client initialization failed."
            ) from exc

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        old_text: str,
        new_text: str,
    ) -> str:
        """
        Build the semantic comparison prompt.

        Only changed chunks should normally reach this method. The prompt
        still explicitly instructs the model to ignore irrelevant
        formatting and to return machine-readable JSON.
        """
        return f"""
You are a legal and business document analyst.

Compare the OLD and NEW versions below.

Your job is to identify meaningful changes in meaning, policy,
commercial terms, obligations, permissions, restrictions, features,
or other business-relevant behavior.

RULES:
1. Identify every meaningful semantic change.
2. Ignore formatting-only, whitespace-only, spelling-only, and
   non-semantic structural changes.
3. Categorize each change as exactly one of:
   pricing, legal, policy, feature, other.
4. Assess each change as exactly one of:
   high, medium, low.
5. Explain the practical or business impact.
6. Do not invent facts that are not supported by the provided text.
7. If there is no meaningful semantic change, return an empty
   "changes" array and set "overall_risk" to "low".
8. Return ONLY valid JSON. Do not use Markdown fences.

OUTPUT SCHEMA:
{{
    "changes": [
        {{
            "category": "pricing|legal|policy|feature|other",
            "old_meaning": "What the old version meant",
            "new_meaning": "What the new version means",
            "impact": "Brief explanation of business impact",
            "risk_level": "high|medium|low"
        }}
    ],
    "summary": "Concise overall summary",
    "overall_risk": "high|medium|low"
}}

OLD VERSION:
{old_text}

NEW VERSION:
{new_text}
"""

    # ------------------------------------------------------------------
    # Input normalization
    # ------------------------------------------------------------------

    def _validate_input_length(
        self,
        text: str,
    ) -> str:
        """
        Reject over-limit evidence instead of silently discarding changes.

        Truncation can omit a material revision and still return a completed
        low-risk result. The detector persists this error as failed analysis.
        """
        if len(text) > self.max_input_chars:
            raise DeepSeekPermanentError(
                "Semantic comparison input exceeds the configured "
                "DEEPSEEK_MAX_INPUT_CHARS limit; no partial analysis "
                "was performed."
            )

        return text

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json(
        raw_content: str,
    ) -> Dict[str, Any]:
        """
        Extract a JSON object from a model response.

        DeepSeek is instructed to return JSON only, but production code
        still tolerates accidental Markdown fences or surrounding text.
        """
        if not raw_content:
            raise DeepSeekSemanticDiffError(
                "DeepSeek returned an empty response."
            )

        content = raw_content.strip()

        if content.startswith("```"):
            lines = content.splitlines()

            if lines:
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            content = "\n".join(lines).strip()

        try:
            parsed = json.loads(content)

            if not isinstance(parsed, dict):
                raise DeepSeekSemanticDiffError(
                    "DeepSeek response JSON must be an object."
                )

            return parsed

        except json.JSONDecodeError:
            pass

        start = content.find("{")
        end = content.rfind("}")

        if start == -1 or end <= start:
            raise DeepSeekSemanticDiffError(
                "DeepSeek response did not contain a valid JSON object."
            )

        json_str = content[
            start:end + 1
        ]

        try:
            parsed = json.loads(json_str)

        except json.JSONDecodeError as exc:
            raise DeepSeekSemanticDiffError(
                "DeepSeek returned malformed JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise DeepSeekSemanticDiffError(
                "DeepSeek response JSON must be an object."
            )

        return parsed

    @classmethod
    def _normalize_result(
        cls,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate and normalize the model result.

        The method intentionally rejects malformed semantic data instead
        of silently manufacturing missing fields.
        """
        if "changes" not in result:
            if "differences" in result:
                result["changes"] = result.pop(
                    "differences"
                )
            else:
                raise DeepSeekSemanticDiffError(
                    "DeepSeek response is missing 'changes'."
                )

        changes = result["changes"]

        if not isinstance(changes, list):
            raise DeepSeekSemanticDiffError(
                "DeepSeek 'changes' must be a list."
            )

        summary = result.get("summary")

        if not isinstance(summary, str) or not summary.strip():
            raise DeepSeekSemanticDiffError(
                "DeepSeek 'summary' must be a nonempty string."
            )

        overall_risk = str(
            result.get("overall_risk", "")
        ).lower().strip()

        if overall_risk not in cls.ALLOWED_RISK_LEVELS:
            raise DeepSeekSemanticDiffError(
                "DeepSeek returned an invalid overall_risk."
            )

        normalized_changes = []

        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                raise DeepSeekSemanticDiffError(
                    "DeepSeek change at index "
                    f"{index} must be an object."
                )

            category = str(
                change.get("category", "")
            ).lower().strip()

            risk_level = str(
                change.get("risk_level", "")
            ).lower().strip()

            if category not in cls.ALLOWED_CATEGORIES:
                raise DeepSeekSemanticDiffError(
                    "DeepSeek change at index "
                    f"{index} has an invalid category."
                )

            if risk_level not in cls.ALLOWED_RISK_LEVELS:
                raise DeepSeekSemanticDiffError(
                    "DeepSeek change at index "
                    f"{index} has an invalid risk_level."
                )

            old_meaning = change.get(
                "old_meaning",
                "",
            )

            new_meaning = change.get(
                "new_meaning",
                "",
            )

            impact = change.get(
                "impact",
                "",
            )

            if not isinstance(old_meaning, str):
                raise DeepSeekSemanticDiffError(
                    "DeepSeek old_meaning must be a string."
                )

            if not isinstance(new_meaning, str):
                raise DeepSeekSemanticDiffError(
                    "DeepSeek new_meaning must be a string."
                )

            if not isinstance(impact, str):
                raise DeepSeekSemanticDiffError(
                    "DeepSeek impact must be a string."
                )

            # Additions and removals may legitimately have one empty side,
            # but a change with no meaning on either side is not evidence.
            if not old_meaning.strip() and not new_meaning.strip():
                raise DeepSeekSemanticDiffError(
                    "DeepSeek change has no old or new meaning."
                )
            if not impact.strip():
                raise DeepSeekSemanticDiffError(
                    "DeepSeek change has no business impact explanation."
                )

            normalized_changes.append(
                {
                    "category": category,
                    "old_meaning": old_meaning,
                    "new_meaning": new_meaning,
                    "impact": impact,
                    "risk_level": risk_level,
                }
            )

        # A successful analysis must not publish contradictory risk
        # evidence. In particular, an empty result is NOT a high-risk
        # finding, and overall risk cannot understate an individual change.
        if not normalized_changes and overall_risk != "low":
            raise DeepSeekSemanticDiffError(
                "DeepSeek empty changes require low overall_risk."
            )
        risk_order = {"low": 0, "medium": 1, "high": 2}
        if any(
            risk_order[change["risk_level"]] > risk_order[overall_risk]
            for change in normalized_changes
        ):
            raise DeepSeekSemanticDiffError(
                "DeepSeek overall_risk understates a reported change."
            )

        return {
            "analysis_status": "success",
            "changes": normalized_changes,
            "summary": summary.strip(),
            "overall_risk": overall_risk,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(
        self,
        old_text: str,
        new_text: str,
    ) -> Dict[str, Any]:
        """
        Compare two text versions and extract semantic differences.

        Raises:
            DeepSeekSemanticDiffError:
                When the external API fails or the response cannot be
                trusted as a valid semantic analysis.
        """
        if not old_text or not new_text:
            raise DeepSeekPermanentError(
                "Cannot perform semantic comparison with empty text."
            )

        old_text = self._validate_input_length(
            old_text
        )

        new_text = self._validate_input_length(
            new_text
        )

        prompt = self._build_prompt(
            old_text,
            new_text,
        )

        try:
            response = (
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a strict JSON generator. "
                                "Return only valid JSON matching "
                                "the requested schema."
                            ),
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0.0,
                    response_format={
                        "type": "json_object"
                    },
                )
            )

        except Exception as exc:
            # Status codes are supplied by the OpenAI-compatible SDK. Do
            # not retry invalid credentials, unsupported models or requests.
            status_code = getattr(exc, "status_code", None)
            if status_code is not None and (
                status_code in {400, 401, 403, 404, 422}
            ):
                raise DeepSeekPermanentError(
                    "DeepSeek request is not retryable; check provider "
                    "credentials, model and request configuration."
                ) from exc

            logger.warning(
                "DeepSeek request failed; bounded retry may be possible "
                "(status=%s).",
                status_code,
            )
            raise DeepSeekTransientError(
                "DeepSeek semantic analysis request failed."
            ) from exc

        try:
            raw_content = (
                response
                .choices[0]
                .message
                .content
            )

        except (
            AttributeError,
            IndexError,
            TypeError,
        ) as exc:
            logger.exception(
                "DeepSeek returned an unexpected response shape."
            )

            raise DeepSeekSemanticDiffError(
                "DeepSeek returned an invalid response."
            ) from exc

        try:
            result = self._extract_json(
                raw_content
            )

            normalized = self._normalize_result(
                result
            )

        except DeepSeekSemanticDiffError:
            logger.exception(
                "DeepSeek response validation failed."
            )
            raise

        # Distinguish the configured name from the model identity returned by
        # the provider. An alias may route to a newer model without a deploy.
        # Usage is optional in SDK responses; never invent a token count.
        reported_model = getattr(response, "model", None)
        if (
            not isinstance(reported_model, str)
            or not reported_model.strip()
            or len(reported_model) > 128
        ):
            reported_model = None
        tokens = getattr(getattr(response, "usage", None), "total_tokens", None)
        if type(tokens) is not int or not 0 <= tokens <= 2_147_483_647:
            tokens = None
        normalized["provider_execution"] = {
            "requested_model": self.model,
            "reported_model": reported_model,
            "usage_reported": tokens is not None,
            "total_tokens": tokens,
        }

        logger.info(
            "DeepSeek semantic analysis completed successfully "
            "(model=%s, changes=%d, risk=%s).",
            self.model,
            len(normalized["changes"]),
            normalized["overall_risk"],
        )

        return normalized


def compare_snapshots(
    old_path: str,
    new_path: str,
) -> Optional[Dict[str, Any]]:
    """
    Compare two snapshot files.

    Returns:
        Valid semantic analysis result on success.
        ``None`` when the comparison cannot be completed.
    """
    try:
        with open(
            old_path,
            "r",
            encoding="utf-8",
        ) as old_file:
            old_text = old_file.read()

        with open(
            new_path,
            "r",
            encoding="utf-8",
        ) as new_file:
            new_text = new_file.read()

        engine = (
            DeepSeekSemanticDiffEngine()
        )

        return engine.compare(
            old_text,
            new_text,
        )

    except DeepSeekSemanticDiffError:
        logger.exception(
            "Semantic comparison failed for snapshots: %s vs %s",
            old_path,
            new_path,
        )
        return None

    except OSError:
        logger.exception(
            "Unable to read snapshot files: %s vs %s",
            old_path,
            new_path,
        )
        return None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO
    )

    data_dir = "data/raw"

    files = [
        filename
        for filename in os.listdir(data_dir)
        if (
            filename.startswith("slack_tos_")
            and filename.endswith(".txt")
        )
    ]

    files.sort()

    if len(files) >= 2:
        old_file = os.path.join(
            data_dir,
            files[-2],
        )

        new_file = os.path.join(
            data_dir,
            files[-1],
        )

        print(
            f"Comparing: {files[-2]} "
            f"vs {files[-1]}"
        )

        result = compare_snapshots(
            old_file,
            new_file,
        )

        if result:
            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(
                "Comparison failed. "
                "Check logs for details."
            )

    else:
        print(
            "Need at least 2 snapshot files "
            "in data/raw/"
        )