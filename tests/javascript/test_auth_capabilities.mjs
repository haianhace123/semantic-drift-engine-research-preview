import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const src = readFileSync(new URL('../../src/static/js/auth-pages.js', import.meta.url), 'utf8');
const start = src.indexOf('async function readAuthCapabilities() {');
const end = src.indexOf('function setupOAuthButtons() {', start);
assert.ok(start > 0 && end > start, 'Expected one capability boundary');
const functions = src.slice(start, end);

function harness(fetch, document) {
  const context = { fetch, document };
  vm.runInNewContext(functions, context);
  return context;
}

function fakeButton() {
  const label = { textContent: 'Google' };
  return {
    disabled: false,
    dataset: {},
    attrs: {},
    label,
    setAttribute(key, val) { this.attrs[key] = val; },
    querySelector(selector) { return selector === '[data-oauth-label]' ? label : null; },
  };
}

test('network failure disables optional auth capabilities', async () => {
  const { readAuthCapabilities } = harness(async () => { throw new Error('offline'); }, {});
  const capabilities = await readAuthCapabilities();
  assert.equal(capabilities.google, false);
  assert.equal(capabilities.github, false);
  assert.equal(capabilities.email_recovery, false);
});

test('only exact boolean true enables a capability', async () => {
  const { readAuthCapabilities } = harness(async () => ({
    ok: true,
    json: async () => ({ google: true, github: 'true', email_recovery: null }),
  }), {});
  const capabilities = await readAuthCapabilities();
  assert.equal(capabilities.google, true);
  assert.equal(capabilities.github, false);
  assert.equal(capabilities.email_recovery, false);
});

test('unconfigured oauth and recovery are visibly disabled', () => {
  const buttons = Object.fromEntries(
    ['googleBtn', 'githubBtn', 'googleLogin', 'githubLogin'].map(id => [id, fakeButton()]),
  );
  const link = {
    textContent: 'Forgot password?',
    attrs: {},
    removeAttribute(k) { this.attrs[k] = null; },
    setAttribute(k, v) { this.attrs[k] = v; },
    get title() { return this.attrs.title; },
    set title(v) { this.attrs.title = v; },
  };
  const document = {
    getElementById: id => buttons[id] || null,
    querySelectorAll: () => [link],
  };
  harness(async () => {}, document).applyAuthCapabilities({
    google: false,
    github: false,
    email_recovery: false,
  });
  for (const button of Object.values(buttons)) {
    assert.equal(button.disabled, true);
    assert.equal(button.dataset.oauthUnavailable, 'true');
    assert.equal(button.label.textContent, 'Unavailable');
  }
  assert.equal(link.attrs.href, null);
  assert.equal(link.attrs['aria-disabled'], 'true');
  assert.equal(link.textContent, 'Recovery unavailable');
});

test('configured oauth remains usable without hiding recovery', () => {
  const google = fakeButton();
  const link = { removeAttribute() { throw Error('Must retain link'); } };
  const ctx = harness(async () => {}, {
    getElementById: id => id === 'googleBtn' ? google : null,
    querySelectorAll: () => [link],
  });
  ctx.applyAuthCapabilities({ google: true, github: true, email_recovery: true });
  assert.equal(google.disabled, false);
});
