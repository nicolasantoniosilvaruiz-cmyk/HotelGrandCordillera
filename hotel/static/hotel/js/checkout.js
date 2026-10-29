document.addEventListener('DOMContentLoaded', () => {
  const banner = document.getElementById('retencion');
  const form = document.getElementById('form-pago');
  if (banner && form) {
    const disponible = Date.parse(banner.dataset.vencimiento) - Date.parse(banner.dataset.ahora);
    const inicio = performance.now();
    const boton = document.getElementById('confirmar-pago');
    const actualizar = () => {
      const segundos = Math.max(0, Math.floor((disponible - (performance.now()-inicio))/1000));
      document.getElementById('tiempo-restante').textContent = `${String(Math.floor(segundos/60)).padStart(2,'0')}:${String(segundos%60).padStart(2,'0')}`;
      banner.querySelector('.timer-display').classList.toggle('urgent',segundos<60);
      if (!segundos) {
        boton.disabled = true;
        boton.textContent = 'Retención vencida';
        document.getElementById('retencion-vencida').hidden = false;
      }
    };
    actualizar();const timer = setInterval(actualizar,1000);
    window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
    form.addEventListener('submit',()=>{
      if(form.checkValidity()){boton.disabled=true;boton.textContent='Confirmando tu reserva…';}
    });
    window.addEventListener('pageshow',()=>{actualizar();if(disponible-(performance.now()-inicio)>0)boton.disabled=false;});
  }
  document.querySelectorAll('[data-copy-code]').forEach(button=>button.addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(button.dataset.copyCode);button.textContent='Copiado ✓';}
    catch{button.textContent='Selecciona el código';}
  }));
  document.querySelectorAll('[data-print]').forEach(button=>button.addEventListener('click',()=>window.print()));
});
