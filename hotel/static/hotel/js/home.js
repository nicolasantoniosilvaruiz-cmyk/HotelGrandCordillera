document.addEventListener('DOMContentLoaded', () => {
  const menu = document.querySelector('.menu-toggle');
  const nav = document.querySelector('#navegacion');
  menu?.addEventListener('click', () => {
    const expanded = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(expanded));
    nav?.classList.toggle('is-open', expanded);
  });
  nav?.querySelectorAll('a, button').forEach(link => link.addEventListener('click', () => {
    nav.classList.remove('is-open');
    menu?.setAttribute('aria-expanded', 'false');
  }));
  document.querySelectorAll('[data-dialog]').forEach(button => {
    button.addEventListener('click', () => document.getElementById(button.dataset.dialog)?.showModal());
  });
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('click', event => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
  });
  const entrada = document.querySelector('#entrada');
  const salida = document.querySelector('#salida');
  const diaSiguiente = value => {
    const fecha = new Date(`${value}T12:00:00`);
    fecha.setDate(fecha.getDate() + 1);
    return `${fecha.getFullYear()}-${String(fecha.getMonth() + 1).padStart(2,'0')}-${String(fecha.getDate()).padStart(2,'0')}`;
  };
  const ajustarSalida = () => {
    if (!entrada?.value || !salida) return;
    const minima = diaSiguiente(entrada.value);
    salida.min = minima;
    if (salida.value <= entrada.value) salida.value = minima;
  };
  entrada?.addEventListener('change', ajustarSalida);
  ajustarSalida();
});
