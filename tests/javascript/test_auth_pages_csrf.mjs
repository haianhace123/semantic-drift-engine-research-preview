import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const script = readFileSync(new URL('../../src/static/js/auth-pages.js', import.meta.url), 'utf8');

function browser({ csrfOk = true } = {}) {
  const listeners = new Map();
  const calls = [];
  let domReady;
  let destination;
  const fields = {
    loginForm: { dataset: {}, addEventListener(event, cb) { listeners.set(event, cb); } },
    loginBtn: {dataset: {}, disabled: false, textContent: 'Sign in', classList: { add() {}, remove() {} }},
    email: { value: 'preview-owner@invalid.test' },
    password: { value: 'SDE-Test-Password-123!' },
    rememberMe: { checked: false },
    error: { style: {}, textContent: '' },
    success: { style: {}, textContent: '' },
  };
  const document = {
    cookie: 'sdepreview_csrf=the-cookie-token',
    addEventListener(event, cb) { if (event === 'DOMContentLoaded') domReady = cb; },
    getElementById(key) { return fields[key] ?? null; },
    querySelectorAll() { return []; },
  };
  const window = {
    location: { search: '', assign(path) { destination = path; } },
    addEventListener() {},
  };
  async function fetch(path, options = {}) {
    calls.push({path, options});
    if (path === '/api/auth/capabilities') {
      assert.equal(options.credentials, 'same-origin');
      return {ok:true, async json(){return {google:false, github:false, email_recovery:false};}};
    }
    if (path === '/api/auth/csrf') {
      return {ok: csrfOk, async json() { return csrfOk ? {csrf_token: 'the-cookie-token'} : {}; }};
    }
    assert.equal(path, '/api/auth/login');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.headers.get('X-CSRF-Token'), 'the-cookie-token');
    assert.equal(options.headers.get('Content-Type'), 'application/json');
    assert.equal(JSON.parse(options.body).email, fields.email.value);
    return {ok:true, async json(){return {ok:true};}};
  }
  runInNewContext(script, { document, window, fetch, Headers, URLSearchParams, setTimeout(fn) { fn(); } });
  return {
    calls, fields,
    async submit() {
      await domReady();
      assert.ok(listeners.has('submit'), 'login submit handler was not installed');
      await listeners.get('submit')({preventDefault(){}});
    },
    get destination() { return destination; },
  };
}

test('login uses response CSRF even with preview-specific cookie name', async () => {
  const preview = browser();
  await preview.submit();
  assert.deepEqual(preview.calls.map(x=>x.path), ['/api/auth/capabilities','/api/auth/csrf','/api/auth/login']);
  assert.equal(preview.destination, '/dashboard/index.html');
});

test('login fails closed when CSRF initialization fails', async () => {
  const preview = browser({csrfOk:false});
  await preview.submit();
  assert.deepEqual(preview.calls.map(x=>x.path), ['/api/auth/capabilities','/api/auth/csrf']);
  assert.equal(preview.destination, undefined);
  assert.match(preview.fields.error.textContent, /secure session/i);
});
