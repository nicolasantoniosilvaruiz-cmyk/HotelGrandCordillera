document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-cancel-form]').forEach(form => {
    form.addEventListener('submit', () => {
      if (form.checkValidity()) {
        const button = form.querySelector('[type=submit]');
        button.disabled = true;
        button.textContent = 'Cancelando…';
      }
    });
    window.addEventListener('pageshow',()=>{
      const button=form.querySelector('[type=submit]');
      button.disabled=false;button.textContent='Cancelar reserva';
    });
  });
});
