(function () {
  'use strict';

  // ============================================================
  // Shared auth-page utilities
  // ============================================================

  // The backend can use a cookie prefix specific to each runtime.
  // Obtain the matching token from the CSRF endpoint, not a hard-coded cookie.
  async function ensureCsrf() {
    const response = await fetch('/api/auth/csrf', {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
    });

    const payload = await response.json().catch(() => ({}));

    if (
      !response.ok ||
      typeof payload.csrf_token !== 'string' ||
      !payload.csrf_token
    ) {
      throw new Error('Unable to establish a secure session. Please retry.');
    }

    return payload.csrf_token;
  }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});

    if (options.body !== undefined) {
      headers.set('Content-Type', 'application/json');
    }

    const method = (options.method || 'GET').toUpperCase();

    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      headers.set('X-CSRF-Token', await ensureCsrf());
    }

    const response = await fetch(path, {
      ...options,
      method,
      headers,
      credentials: 'same-origin',
    });

    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      const error =
        payload?.error ||
        payload?.detail?.error;

      const detail =
        payload?.detail;

      const text =
        error?.message ||
        (
          typeof detail === 'string'
            ? detail
            : ''
        ) ||
        response.statusText ||
        'Request failed.';

      const wrapped =
        new Error(text);

      wrapped.code =
        error?.code ||
        payload?.code ||
        null;

      wrapped.status =
        response.status;

      wrapped.details =
        error?.details ||
        payload?.details ||
        null;

      if (
        wrapped.details &&
        wrapped.details.retry_after !==
          undefined
      ) {
        wrapped.retry_after =
          wrapped.details.retry_after;
      }

      throw wrapped;
    }

    return payload;
  }

  function showMessage(type, text) {
    const error = document.getElementById('error');
    const success = document.getElementById('success');

    if (error) {
      error.style.display = type === 'error' ? 'block' : 'none';

      if (type === 'error') {
        error.textContent = text;
      }
    }

    if (success) {
      success.style.display = type === 'success' ? 'block' : 'none';

      if (type === 'success') {
        success.textContent = text;
      }
    }
  }

  function clearMessages() {
    const error = document.getElementById('error');
    const success = document.getElementById('success');

    if (error) {
      error.style.display = 'none';
    }

    if (success) {
      success.style.display = 'none';
    }
  }

  function setLoading(button, loading, loadingText) {
    if (!button) {
      return;
    }

    if (loading) {
      if (!button.dataset.originalText) {
        button.dataset.originalText =
          button.textContent.trim();
      }

      button.disabled = true;
      button.textContent = loadingText;
      button.classList.add('is-loading');
      return;
    }

    button.disabled = false;

    button.textContent =
      button.dataset.originalText ||
      button.textContent;

    button.classList.remove('is-loading');
  }

  function redirectAfterLogin() {
    window.location.assign('/dashboard/index.html');
  }


  // ============================================================
  // Password visibility / strength
  // ============================================================

  function passwordRequirements(value) {
    // Match the public input requirements enforced by validate_password
    // in src/api/auth_routes.py. The server makes the final decision.
    return [
      [value.length >= 12, '12 or more characters'],
      [/[A-Z]/.test(value), 'an uppercase letter'],
      [/[a-z]/.test(value), 'a lowercase letter'],
      [/\d/.test(value), 'a number'],
      [/[^A-Za-z0-9]/.test(value), 'a symbol'],
    ];
  }

  function missingPasswordRequirements(value) {
    return passwordRequirements(value)
      .filter(([met]) => !met)
      .map(([, description]) => description);
  }

  function setupPasswordWidgets() {
    document
      .querySelectorAll('[data-toggle-password]')
      .forEach((button) => {
        const inputId =
          button.dataset.togglePassword;

        const input =
          document.getElementById(inputId);

        if (!input) {
          return;
        }

        if (
          button.dataset.passwordBound === 'true'
        ) {
          return;
        }

        button.dataset.passwordBound =
          'true';

        button.addEventListener(
          'click',
          (event) => {
            event.preventDefault();
            event.stopPropagation();

            const shouldShow =
              input.type === 'password';

            input.type =
              shouldShow
                ? 'text'
                : 'password';

            button.textContent =
              shouldShow
                ? 'Hide'
                : 'Show';

            button.setAttribute(
              'aria-label',
              shouldShow
                ? 'Hide password'
                : 'Show password'
            );
          }
        );
      });

    const password =
      document.getElementById('password');

    const strength =
      document.getElementById(
        'passwordStrength'
      );

    if (
      password &&
      strength
    ) {
      const update = () => {
        const value =
          password.value;

        const conditions = passwordRequirements(value);
        const missing = missingPasswordRequirements(value);
        const complete = value.length > 0 && missing.length === 0;

        if (!value) {
          strength.textContent =
            'Use 12+ characters with uppercase, lowercase, a number and a symbol.';
        } else if (complete) {
          strength.textContent = 'Meets password requirements.';
        } else {
          strength.textContent = `Still needed: ${missing.join(', ')}.`;
        }
        strength.dataset.valid = String(complete);

        // The five segments represent policy conditions, not estimated
        // resistance to guessing. Only the backend accepts passwords.
        const meter = document.getElementById('passwordStrengthMeter');
        if (meter) {
          meter.dataset.score = String(conditions.length - missing.length);
        }
      };

      password.addEventListener(
        'input',
        update
      );

      update();
    }

    const email =
      document.getElementById('email');

    const emailHint =
      document.getElementById(
        'emailHint'
      );

    if (
      email &&
      emailHint
    ) {
      email.addEventListener(
        'input',
        () => {
          const value =
            email.value.trim();

          if (!value) {
            emailHint.textContent = '';
            return;
          }

          const valid =
            /^[^\s@]+@[^\s@]+\.[^\s@]+$/
              .test(value);

          emailHint.textContent =
            valid
              ? 'Valid email'
              : 'Enter a valid email address';

          emailHint.dataset.valid =
            String(valid);
        }
      );
    }
  }


// ============================================================
// OAuth
// ============================================================

function resetOAuthButtons() {
  const buttons = [
    [
      'googleBtn',
      'Continue with Google',
    ],
    [
      'githubBtn',
      'Continue with GitHub',
    ],
    [
      'googleLogin',
      'Continue with Google',
    ],
    [
      'githubLogin',
      'Continue with GitHub',
    ],
  ];

  buttons.forEach(
    ([id, defaultText]) => {
      const button =
        document.getElementById(id);

      if (!button) {
        return;
      }

      if (button.dataset.oauthUnavailable === 'true') {
        button.disabled = true;
        return;
      }

      button.disabled = false;

      button.classList.remove(
        'is-loading'
      );

      button.removeAttribute(
        'aria-busy'
      );

      const label =
        button.querySelector(
          '[data-oauth-label]'
        );

      if (label) {
        label.textContent =
          defaultText;
      }
    }
  );
}

async function readAuthCapabilities() {
  // Network failure must never imply a feature is available.
  try {
    const response = await fetch('/api/auth/capabilities', {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('Capabilities unavailable');
    const payload = await response.json();
    return {
      google: payload?.google === true,
      github: payload?.github === true,
      email_recovery: payload?.email_recovery === true,
    };
  } catch (_) {
    return { google: false, github: false, email_recovery: false };
  }
}

function applyAuthCapabilities(capabilities) {
  for (const [provider, ids] of [
    ['google', ['googleBtn', 'googleLogin']],
    ['github', ['githubBtn', 'githubLogin']],
  ]) {
    if (capabilities[provider]) continue;
    for (const id of ids) {
      const button = document.getElementById(id);
      if (!button) continue;
      button.disabled = true;
      button.dataset.oauthUnavailable = 'true';
      button.setAttribute('aria-label', `${provider} sign-in unavailable`);
      const label = button.querySelector('[data-oauth-label]');
      if (label) label.textContent = 'Unavailable';
    }
  }

  if (!capabilities.email_recovery) {
    for (const link of document.querySelectorAll('a[href="/forgot-password.html"]')) {
      link.removeAttribute('href');
      link.setAttribute('aria-disabled', 'true');
      link.textContent = 'Recovery unavailable';
      link.title = 'Email recovery has not been enabled for this environment';
    }
  }
}

function setupOAuthButtons() {
  const providers = [
    {
      ids: [
        'googleBtn',
        'googleLogin',
      ],
      endpoint:
        '/api/auth/google',
      loadingText:
        'Connecting to Google...',
    },
    {
      ids: [
        'githubBtn',
        'githubLogin',
      ],
      endpoint:
        '/api/auth/github',
      loadingText:
        'Connecting to GitHub...',
    },
  ];

  providers.forEach(
    ({
      ids,
      endpoint,
      loadingText,
    }) => {
      ids.forEach((id) => {
        const button =
          document.getElementById(id);

        if (!button) {
          return;
        }

        /*
         * Avoid attaching duplicate handlers
         * when the page is restored/reinitialized.
         */
        if (
          button.dataset.oauthBound ===
          'true'
        ) {
          return;
        }

        button.dataset.oauthBound =
          'true';

        button.addEventListener(
          'click',
          (event) => {
            event.preventDefault();

            clearMessages();

            /*
             * Show a temporary loading state.
             * The browser will leave this page immediately
             * after navigation starts.
             */
            button.disabled = true;

            button.classList.add(
              'is-loading'
            );

            button.setAttribute(
              'aria-busy',
              'true'
            );

            const label =
              button.querySelector(
                '[data-oauth-label]'
              );

            if (label) {
              label.textContent =
                loadingText;
            }

            /*
             * Use normal browser navigation for OAuth.
             * FastAPI will issue the provider redirect.
             */
            window.location.href =
              endpoint;
          }
        );
      });
    }
  );
}

// ============================================================
// Restore OAuth buttons after browser Back / Forward
// ============================================================

window.addEventListener(
  'pageshow',
  () => {
    resetOAuthButtons();
  }
);


  // ============================================================
  // Login
  // ============================================================

  async function setupLogin() {
    const form =
      document.getElementById(
        'loginForm'
      );

    if (
      !form ||
      form.dataset.bound === 'true'
    ) {
      return;
    }

    form.dataset.bound =
      'true';

    const params =
      new URLSearchParams(
        window.location.search
      );

    if (
      params.get('verified') === '1'
    ) {
      showMessage(
        'success',
        'Email verified. You can sign in now.'
      );
    }

    form.addEventListener(
      'submit',
      async (event) => {
        event.preventDefault();

        clearMessages();

        const button =
          document.getElementById(
            'loginBtn'
          );

        const email =
          document
            .getElementById('email')
            ?.value
            .trim()
            .toLowerCase();

        const password =
          document
            .getElementById('password')
            ?.value ||
          '';

        const rememberMe =
          !!document.getElementById(
            'rememberMe'
          )?.checked;

        if (
          !email ||
          !password
        ) {
          showMessage(
            'error',
            'Please enter your email and password.'
          );

          return;
        }

        setLoading(
          button,
          true,
          'Signing in…'
        );

        try {
          await api(
            '/api/auth/login',
            {
              method:
                'POST',

              body:
                JSON.stringify({
                  email,
                  password,
                  remember_me:
                    rememberMe,
                }),
            }
          );

          showMessage(
            'success',
            'Signed in successfully. Redirecting…'
          );

          setTimeout(
            redirectAfterLogin,
            250
          );
        } catch (error) {
          showMessage(
            'error',
            error?.message ||
              'Unable to sign in. Please try again.'
          );

          setLoading(
            button,
            false,
            'Signing in…'
          );
        }
      }
    );
  }


  // ============================================================
  // Signup + six-digit email verification
  // ============================================================

  async function setupSignup() {
    const form =
      document.getElementById(
        'signupForm'
      );

    if (
      !form ||
      form.dataset.bound === 'true'
    ) {
      return;
    }

    form.dataset.bound =
      'true';

    const verifyPanel =
      document.getElementById(
        'verifyPanel'
      );

    const verifyForm =
      document.getElementById(
        'verifyForm'
      );

    form.addEventListener(
      'submit',
      async (event) => {
        event.preventDefault();

        clearMessages();

        const button =
          document.getElementById(
            'signupBtn'
          );

        const email =
          document
            .getElementById('email')
            ?.value
            .trim()
            .toLowerCase() ||
          '';

        const password =
          document
            .getElementById('password')
            ?.value ||
          '';

        const confirmPassword =
          document
            .getElementById(
              'confirmPassword'
            )
            ?.value ||
          '';

        const fullName =
          document
            .getElementById('fullName')
            ?.value
            .trim() ||
          '';

        const termsAccepted =
          !!document.getElementById(
            'termsAccepted'
          )?.checked;

        if (
          !/^[^\s@]+@[^\s@]+\.[^\s@]+$/
            .test(email)
        ) {
          showMessage(
            'error',
            'Please enter a valid email address.'
          );

          return;
        }

        const missingRequirements = missingPasswordRequirements(password);
        if (missingRequirements.length > 0) {
          showMessage(
            'error',
            `Password requirements not met: ${missingRequirements.join(', ')}.`
          );

          return;
        }

        if (
          password !==
          confirmPassword
        ) {
          showMessage(
            'error',
            'Passwords do not match.'
          );

          return;
        }

        if (
          !termsAccepted
        ) {
          showMessage(
            'error',
            'Please accept the Terms of Service and Privacy Policy.'
          );

          return;
        }

        function showVerificationStage(queued) {
          form.style.display = 'none';
          const signupStage = document.getElementById('signupStage');
          const signupHeader = document.getElementById('signupHeader');
          if (signupStage) signupStage.hidden = true;
          if (signupHeader) signupHeader.hidden = true;
          if (verifyPanel) {
            verifyPanel.hidden = false;
            verifyPanel.style.display = 'block';
          }
          const verifyEmail = document.getElementById('verifyEmail');
          if (verifyEmail) verifyEmail.textContent = email;
          sessionStorage.setItem('verification_email', email);
          if (queued) startResendCountdown(60);
          showMessage(
            queued ? 'success' : 'error',
            queued
              ? 'Verification email queued. Check your inbox.'
              : 'Account created, but email could not be queued. Use Resend code.'
          );
          document.getElementById('verificationCode')?.focus();
        }

        setLoading(
          button,
          true,
          'Creating account…'
        );

        try {
          await api(
            '/api/auth/signup',
            {
              method:
                'POST',

              body:
                JSON.stringify({
                  email,
                  password,
                  confirm_password:
                    confirmPassword,
                  full_name:
                    fullName,
                  terms_accepted:
                    true,
                }),
            }
          );

          showVerificationStage(true);
        } catch (error) {
          if (error?.code === 'AUTH_019' &&
              error?.details?.account_created === true) {
            showVerificationStage(false);
          } else {
            showMessage(
              'error',
              error?.message ||
                'Unable to create account.'
            );
          }
        } finally {
          setLoading(
            button,
            false,
            'Creating account…'
          );
        }
      }
    );

    if (
      verifyForm &&
      verifyForm.dataset.bound !==
        'true'
    ) {
      verifyForm.dataset.bound =
        'true';

      verifyForm.addEventListener(
        'submit',
        async (event) => {
          event.preventDefault();

          clearMessages();

          const button =
            document.getElementById(
              'verifyBtn'
            );

          const email =
            document
              .getElementById(
                'verifyEmail'
              )
              ?.textContent
              .trim()
              .toLowerCase() ||
            sessionStorage.getItem(
              'verification_email'
            ) ||
            '';

          const code =
            document
              .getElementById(
                'verificationCode'
              )
              ?.value
              .trim() ||
            '';

          if (
            !/^\d{6}$/.test(code)
          ) {
            showMessage(
              'error',
              'Enter the 6-digit verification code.'
            );

            return;
          }

          setLoading(
            button,
            true,
            'Verifying…'
          );

          try {
            await api(
              '/api/auth/verify-otp',
              {
                method:
                  'POST',

                body:
                  JSON.stringify({
                    email,
                    code,
                  }),
              }
            );

            sessionStorage.removeItem(
              'verification_email'
            );

            showMessage(
              'success',
              'Email verified. You can sign in now.'
            );

            setTimeout(
              () => {
                window.location.assign(
                  '/login.html?verified=1'
                );
              },
              500
            );
          } catch (error) {
            const message =
              error?.code === 'AUTH_018'
                ? (
                    error?.message ||
                    'Too many verification attempts. Please wait before trying again.'
                  )
                : (
                    error?.message ||
                    'Verification failed.'
                  );

            showMessage(
              'error',
              message
            );

            setLoading(
              button,
              false,
              'Verifying...'
            );
          }
        }
      );
    }

    const resendButton =
      document.getElementById(
        'resendVerificationBtn'
      );

    const resendStatus =
      document.getElementById(
        'resendStatus'
      );

    let resendTimer = null;

    function startResendCountdown(
      seconds = 60
    ) {
      if (!resendButton) {
        return;
      }

      if (resendTimer) {
        window.clearInterval(
          resendTimer
        );

        resendTimer = null;
      }

      let remaining =
        Math.max(
          0,
          Math.ceil(
            Number(seconds) || 0
          )
        );

      resendButton.classList.remove(
        'is-loading'
      );

      resendButton.textContent =
        resendButton.dataset.originalText ||
        'Resend code';

      if (remaining <= 0) {
        resendButton.disabled = false;

        if (resendStatus) {
          resendStatus.textContent =
            'You can request a new code.';
        }

        return;
      }

      resendButton.disabled = true;

      if (resendStatus) {
        resendStatus.textContent =
          `Resend available in ${remaining}s`;
      }

      resendTimer =
        window.setInterval(
          () => {
            remaining -= 1;

            if (remaining <= 0) {
              window.clearInterval(
                resendTimer
              );

              resendTimer = null;

              resendButton.disabled =
                false;

              if (resendStatus) {
                resendStatus.textContent =
                  'You can request a new code.';
              }

              return;
            }

            if (resendStatus) {
              resendStatus.textContent =
                `Resend available in ${remaining}s`;
            }
          },
          1000
        );
    }

    if (
      resendButton &&
      resendButton.dataset.bound !==
        'true'
    ) {
      resendButton.dataset.bound =
        'true';

      resendButton.addEventListener(
        'click',
        async () => {
          clearMessages();

          const verifyEmail =
            document.getElementById(
              'verifyEmail'
            );

          const email =
            (
              verifyEmail?.textContent ||
              sessionStorage.getItem(
                'verification_email'
              ) ||
              ''
            )
              .trim()
              .toLowerCase();

          if (!email) {
            showMessage(
              'error',
              'Verification email is missing. Please sign up again.'
            );

            return;
          }

          if (
            !resendButton.dataset
              .originalText
          ) {
            resendButton.dataset
              .originalText =
              resendButton.textContent
                .trim() ||
              'Resend code';
          }

          resendButton.disabled = true;

          resendButton.classList.add(
            'is-loading'
          );

          resendButton.textContent =
            'Sending...';

          let countdownStarted =
            false;

          try {
            await api(
              '/api/auth/resend-verification',
              {
                method: 'POST',

                body:
                  JSON.stringify({
                    email
                  })
              }
            );

            showMessage(
              'success',
              'If verification is needed, a code may arrive. If not, try again later.'
            );

            countdownStarted = true;

            startResendCountdown(60);

            document
              .getElementById(
                'verificationCode'
              )
              ?.focus();

          } catch (error) {
            const retryAfter =
              Math.max(
                0,
                Math.ceil(
                  Number(
                    error?.details
                      ?.retry_after ||
                    error?.retry_after ||
                    0
                  )
                )
              );

            if (
              error?.code ===
              'AUTH_017'
            ) {
              countdownStarted = true;

              startResendCountdown(
                retryAfter > 0
                  ? retryAfter
                  : 60
              );
            }

            showMessage(
              'error',
              error?.message ||
                'Unable to resend verification code.'
            );

          } finally {
            if (!countdownStarted) {
              resendButton.classList.remove(
                'is-loading'
              );

              resendButton.textContent =
                resendButton.dataset
                  .originalText ||
                'Resend code';

              resendButton.disabled =
                false;
            }
          }
        }
      );
    }

    const verificationCode =
      document.getElementById(
        'verificationCode'
      );

    if (verificationCode) {
      verificationCode.addEventListener(
        'input',
        () => {
          verificationCode.value =
            verificationCode.value
              .replace(
                /\D/g,
                ''
              )
              .slice(
                0,
                6
              );
        }
      );
    }

    const confirmPassword =
      document.getElementById(
        'confirmPassword'
      );

    const confirmHint =
      document.getElementById(
        'confirmHint'
      );

    if (
      confirmPassword &&
      confirmHint
    ) {
      confirmPassword.addEventListener(
        'input',
        () => {
          const password =
            document
              .getElementById(
                'password'
              )
              ?.value ||
            '';

          if (
            !confirmPassword.value
          ) {
            confirmHint.textContent =
              '';
          } else {
            confirmHint.textContent =
              confirmPassword.value ===
              password
                ? 'Passwords match'
                : 'Passwords do not match';
          }
        }
      );
    }
  }


  // ============================================================
  // Forgot password
  // ============================================================

  async function setupForgot(emailRecoveryReady) {
    const form =
      document.getElementById(
        'forgotForm'
      );

    if (
      !form ||
      form.dataset.bound === 'true'
    ) {
      return;
    }

    form.dataset.bound = 'true';

    if (!emailRecoveryReady) {
      form.hidden = true;
      showMessage('error', 'Password recovery is not available in this environment.');
      return;
    }

    const emailInput =
      document.getElementById(
        'email'
      );

    const emailHint =
      document.getElementById(
        'emailHint'
      );

    const button =
      document.getElementById(
        'forgotBtn'
      );

    const sentPanel =
      document.getElementById(
        'forgotSentPanel'
      );

    const sentEmail =
      document.getElementById(
        'forgotSentEmail'
      );

    const againButton =
      document.getElementById(
        'forgotAgainBtn'
      );

    const emailIsValid = (
      value
    ) =>
      /^[^\s@]+@[^\s@]+\.[^\s@]+$/
        .test(value);

    const updateEmailHint = () => {
      if (
        !emailInput ||
        !emailHint
      ) {
        return;
      }

      const email =
        emailInput.value
          .trim()
          .toLowerCase();

      if (!email) {
        emailHint.textContent = '';
        return;
      }

      emailHint.textContent =
        emailIsValid(email)
          ? ''
          : 'Enter a valid email address.';
    };

    emailInput?.addEventListener(
      'input',
      updateEmailHint
    );

    againButton?.addEventListener(
      'click',
      () => {
        clearMessages();

        if (sentPanel) {
          sentPanel.hidden = true;
        }

        form.hidden = false;

        if (emailInput) {
          emailInput.value = '';
          emailInput.focus();
        }

        if (emailHint) {
          emailHint.textContent = '';
        }
      }
    );

    form.addEventListener(
      'submit',
      async (event) => {
        event.preventDefault();

        clearMessages();

        const email =
          emailInput?.value
            .trim()
            .toLowerCase() ||
          '';

        if (
          !emailIsValid(email)
        ) {
          if (emailHint) {
            emailHint.textContent =
              'Enter a valid email address.';
          }

          showMessage(
            'error',
            'Please enter a valid email address.'
          );

          emailInput?.focus();
          return;
        }

        if (emailHint) {
          emailHint.textContent = '';
        }

        setLoading(
          button,
          true,
          'Sending...'
        );

        try {
          await api(
            '/api/auth/forgot-password',
            {
              method: 'POST',

              body:
                JSON.stringify({
                  email,
                }),
            }
          );
        } catch (error) {
          if (error?.status === 503 || error?.code === 'AUTH_021') {
            showMessage('error', 'Password recovery is temporarily unavailable.');
            return;
          }
          // For account-specific failures, do not reveal account existence.
        } finally {
          setLoading(
            button,
            false,
            'Sending...'
          );
        }

        clearMessages();

        if (sentEmail) {
          sentEmail.textContent =
            email;
        }

        form.hidden = true;

        if (sentPanel) {
          sentPanel.hidden = false;
        }
      }
    );
  }


  // ============================================================
  // Reset password
  // ============================================================

  async function setupReset() {
    const form =
      document.getElementById(
        'resetForm'
      );

    if (
      !form ||
      form.dataset.bound === 'true'
    ) {
      return;
    }

    form.dataset.bound = 'true';

    const button =
      document.getElementById(
        'resetBtn'
      );

    const passwordInput =
      document.getElementById(
        'password'
      );

    const confirmPasswordInput =
      document.getElementById(
        'confirmPassword'
      );

    const confirmHint =
      document.getElementById(
        'confirmHint'
      );

    const invalidPanel =
      document.getElementById(
        'resetInvalidPanel'
      );

    const successPanel =
      document.getElementById(
        'resetSuccessPanel'
      );

    const footer =
      document.getElementById(
        'resetFooter'
      );

    const token =
      new URLSearchParams(
        window.location.search
      ).get(
        'token'
      )?.trim() ||
      '';

    if (!token) {
      form.hidden = true;

      if (invalidPanel) {
        invalidPanel.hidden = false;
      }

      if (footer) {
        footer.hidden = true;
      }

      return;
    }

    const updateConfirmHint = () => {
      if (
        !confirmPasswordInput ||
        !confirmHint
      ) {
        return;
      }

      const confirmation =
        confirmPasswordInput.value;

      if (!confirmation) {
        confirmHint.textContent = '';
        return;
      }

      confirmHint.textContent =
        confirmation ===
        (passwordInput?.value || '')
          ? 'Passwords match'
          : 'Passwords do not match';
    };

    passwordInput?.addEventListener(
      'input',
      updateConfirmHint
    );

    confirmPasswordInput
      ?.addEventListener(
        'input',
        updateConfirmHint
      );

    form.addEventListener(
      'submit',
      async (event) => {
        event.preventDefault();

        clearMessages();

        const password =
          passwordInput?.value ||
          '';

        const confirmPassword =
          confirmPasswordInput
            ?.value ||
          '';

        const missingRequirements = missingPasswordRequirements(password);
        if (missingRequirements.length > 0) {
          showMessage(
            'error',
            `Password requirements not met: ${missingRequirements.join(', ')}.`
          );

          passwordInput?.focus();
          return;
        }

        if (
          password !==
          confirmPassword
        ) {
          showMessage(
            'error',
            'Passwords do not match.'
          );

          confirmPasswordInput
            ?.focus();

          return;
        }

        setLoading(
          button,
          true,
          'Updating...'
        );

        try {
          await api(
            '/api/auth/reset-password',
            {
              method: 'POST',

              body:
                JSON.stringify({
                  token,
                  password,

                  confirm_password:
                    confirmPassword,
                }),
            }
          );

          clearMessages();

          form.hidden = true;

          if (footer) {
            footer.hidden = true;
          }

          if (successPanel) {
            successPanel.hidden =
              false;
          }
        } catch (error) {
          const code =
            error?.code ||
            '';

          if (
            code === 'AUTH_009' ||
            code === 'AUTH_004' ||
            code === 'AUTH_005'
          ) {
            form.hidden = true;

            if (footer) {
              footer.hidden = true;
            }

            if (invalidPanel) {
              invalidPanel.hidden =
                false;
            }

            return;
          }

          showMessage(
            'error',
            error?.message ||
              'Password reset failed.'
          );
        } finally {
          setLoading(
            button,
            false,
            'Updating...'
          );
        }
      }
    );
  }


  // ============================================================
  // OAuth account linking confirmation
  // ============================================================

  async function setupOAuthLink() {
    const confirmButton =
      document.getElementById(
        'confirmLinkBtn'
      );

    const cancelButton =
      document.getElementById(
        'cancelLinkBtn'
      );

    if (
      !confirmButton &&
      !cancelButton
    ) {
      return;
    }

    // A cookie alone is not a UI confirmation. Bind the displayed account and
    // provider to the server-side pending link before enabling consent.
    if (confirmButton) {
      confirmButton.disabled = true;
    }

    const emailLabel = document.getElementById('linkEmail');
    const providerLabel = document.getElementById('linkProvider');
    if (confirmButton && emailLabel && providerLabel) {
      try {
        const status = await api('/api/auth/oauth/link-status', {
          cache: 'no-store',
        });
        const providerNames = { google: 'Google', github: 'GitHub' };
        const providerName = providerNames[status?.provider];
        if (
          typeof status?.email !== 'string' ||
          !status.email.trim() ||
          !providerName
        ) {
          throw new Error('The pending account link is unavailable. Start again.');
        }
        emailLabel.textContent = status.email;
        providerLabel.textContent = providerName;
        confirmButton.disabled = false;
      } catch (_) {
        emailLabel.textContent = 'Unavailable';
        providerLabel.textContent = 'Unavailable';
        showMessage('error', 'This account-link request could not be verified. Cancel and start sign in again.');
      }
    }

    if (confirmButton) {
      confirmButton.addEventListener(
        'click',
        async () => {
          setLoading(
            confirmButton,
            true,
            'Linking…'
          );

          try {
            await api(
              '/api/auth/oauth/link/confirm',
              {
                method:
                  'POST',

                body:
                  JSON.stringify({
                    confirm: true,
                  }),
              }
            );

            window.location.assign(
              '/dashboard/index.html'
            );
          } catch (error) {
            showMessage(
              'error',
              error?.message ||
                'Unable to link account.'
            );

            setLoading(
              confirmButton,
              false,
              'Link account'
            );
          }
        }
      );
    }

    if (cancelButton) {
      cancelButton.addEventListener(
        'click',
        async () => {
          try {
            await api(
              '/api/auth/oauth/link/confirm',
              {
                method:
                  'POST',

                body:
                  JSON.stringify({
                    confirm: false,
                  }),
              }
            );
          } catch (_) {
            // Ignore cancellation cleanup errors.
          }

          window.location.assign(
            '/login.html'
          );
        }
      );
    }
  }


  // ============================================================
  // Initialization
  // ============================================================

  document.addEventListener(
    'DOMContentLoaded',
    async () => {
      setupPasswordWidgets();

      const capabilities = await readAuthCapabilities();

      resetOAuthButtons();
      applyAuthCapabilities(capabilities);
      setupOAuthButtons();

      await setupLogin();

      await setupSignup();

      await setupForgot(capabilities.email_recovery);

      await setupReset();

      await setupOAuthLink();
    }
  );
})();