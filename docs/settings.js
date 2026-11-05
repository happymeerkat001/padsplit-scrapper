(() => {
  const input = document.getElementById('compact-density');
  const status = document.getElementById('settings-status');
  try {
    resolveOperationsRuntime(window.PADSPLIT_SETTINGS_CONFIG, window.PADSPLIT_SETTINGS_API_REQUIRED);
  } catch (_) {
    document.querySelectorAll('.settings-card input, .settings-card select, .settings-card button').forEach(element => { element.disabled = true; });
    status.textContent = 'Dashboard configuration is unavailable. Display settings are disabled.';
    return;
  }
  input.checked = document.documentElement.dataset.density === 'compact';
  input.addEventListener('change', () => {
    status.textContent = setOperationsDensity(input.checked)
      ? 'Display preference saved on this browser.'
      : 'Display preference changed for this page, but browser storage is unavailable.';
  });
  const timezone=document.getElementById('display-timezone');
  const dateFormat=document.getElementById('display-date-format');
  const saved=getDisplayPreferences(); timezone.value=saved.timezone; dateFormat.value=saved.dateFormat;
  document.getElementById('save-display-preferences').addEventListener('click',()=>{
    status.textContent=setDisplayPreferences({timezone:timezone.value,dateFormat:dateFormat.value})
      ? 'Display preferences saved on this browser. Reload other open pages to apply them.'
      : 'Display preferences could not be saved. Check browser storage and try again.';
  });
})();
