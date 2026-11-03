/* Shared Firebase email-password reset UI. The injected sender is never called on import. */
const RESET_CONFIRMATION = 'If an eligible account exists for this email, you’ll receive password reset instructions.';
function validResetEmail(value) {
  const email = String(value || '').trim();
  return email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}
function resetErrorMessage(error) {
  if (error?.code === 'auth/network-request-failed') return 'Network error. Check your connection and try again.';
  if (error?.code === 'auth/too-many-requests') return 'Too many requests. Please wait and try again.';
  if (['auth/user-not-found', 'auth/user-disabled', 'auth/invalid-credential'].includes(error?.code)) return RESET_CONFIRMATION;
  return 'Unable to request a reset right now. Please try again later.';
}
async function requestPasswordReset(sender, auth, value) {
  const email = String(value || '').trim();
  if (!validResetEmail(email)) throw Object.assign(new Error('Invalid email'), {code:'auth/invalid-email'});
  try { await sender(auth, email); return RESET_CONFIRMATION; }
  catch (error) { return resetErrorMessage(error); }
}
function mountPasswordReset({auth, sender, form, signInEmail}) {
  if (!form || form.dataset.resetMounted === 'true') return;
  form.dataset.resetMounted = 'true';
  const trigger = document.createElement('button');
  trigger.type = 'button'; trigger.className = 'reset-link'; trigger.textContent = 'Forgot password?';
  const reset = document.createElement('form'); reset.className = 'password-reset-form'; reset.hidden = true;
  reset.setAttribute('aria-label', 'Reset password'); reset.noValidate = true;
  const title = document.createElement('h2'); title.textContent = 'Reset password';
  const info = document.createElement('p'); info.textContent = 'Use the email for your password-based account. If you sign in with another provider, use that provider’s recovery process.';
  const label = document.createElement('label'); label.htmlFor = 'reset-email'; label.textContent = 'Email';
  const email = document.createElement('input'); email.id = 'reset-email'; email.type = 'email'; email.autocomplete = 'email'; email.required = true;
  const feedback = document.createElement('p'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
  const submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = 'Request reset';
  const back = document.createElement('button'); back.type = 'button'; back.className = 'reset-back'; back.textContent = 'Back to sign in';
  reset.append(title, info, label, email, feedback, submit, back);
  form.after(trigger, reset);
  trigger.addEventListener('click', () => {
    form.hidden = true; trigger.hidden = true; reset.hidden = false; feedback.textContent = '';
    email.value = signInEmail?.value || ''; email.focus();
  });
  back.addEventListener('click', () => {
    reset.hidden = true; form.hidden = false; trigger.hidden = false; feedback.textContent = ''; trigger.focus();
  });
  reset.addEventListener('submit', async event => {
    event.preventDefault(); if (submit.disabled) return;
    if (!validResetEmail(email.value)) { feedback.textContent = 'Enter a valid email address.'; email.focus(); return; }
    submit.disabled = true; submit.textContent = 'Requesting…'; feedback.textContent = '';
    try { feedback.textContent = await requestPasswordReset(sender, auth, email.value); }
    finally { submit.disabled = false; submit.textContent = 'Request reset'; }
  });
}
