document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-recipient-action]').forEach(boton => {
    const grupo = boton.closest('.recipients-field');
    const casillas = grupo.querySelectorAll('input[type="checkbox"][name="clientes"]:enabled');
    boton.disabled = casillas.length === 0;
    boton.addEventListener('click', () => {
      const marcar = boton.dataset.recipientAction === 'select';
      casillas.forEach(casilla => { casilla.checked = marcar; });
    });
  });

  const buscador = document.querySelector('[data-recipient-search]');
  if (buscador) {
    const grupo = buscador.closest('.recipients-field');
    const normalizar = texto => texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
    const compactarRut = texto => texto.replace(/[.\s-]/g, '');
    const filas = Array.from(grupo.querySelectorAll('[data-recipient-row]'), fila => {
      const [nombre, rut = ''] = normalizar(fila.textContent).split(' · rut ');
      return {fila, nombre, rut: compactarRut(rut)};
    });
    const sinResultados = grupo.querySelector('[data-recipient-empty]');

    const filtrar = () => {
      const consulta = normalizar(buscador.value);
      const rut = compactarRut(consulta);
      let coincidencias = 0;
      filas.forEach(item => {
        const coincide = item.nombre.includes(consulta) || (rut !== '' && item.rut.includes(rut));
        item.fila.hidden = !coincide;
        if (coincide) coincidencias += 1;
      });
      sinResultados.hidden = coincidencias > 0 || filas.length === 0;
    };
    buscador.addEventListener('input', filtrar);
    buscador.addEventListener('keydown', evento => {
      if (evento.key === 'Enter') evento.preventDefault();
    });
    filtrar();
  }

  const dashboard=document.getElementById('dashboard-live');
  let esperando=false;
  async function actualizar(){
    if(!dashboard||document.hidden||esperando)return;
    esperando=true;
    try{
      const response=await fetch(dashboard.dataset.url,{headers:{'Accept':'application/json'},cache:'no-store'});
      if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw new Error('No disponible');
      const data=await response.json();
      if(document.querySelectorAll('[data-room]').length!==data.habitaciones.length&&!document.querySelector('.rooms-map details[open]')){window.location.reload();return;}
      Object.entries(data.conteo).forEach(([estado,total])=>{const node=document.querySelector(`[data-count="${estado}"]`);if(node)node.textContent=total;});
      data.habitaciones.forEach(room=>{
        const card=document.querySelector(`[data-room="${room.id}"]`);if(!card)return;
        ['disponible','reservada','ocupada','mantenimiento'].forEach(c=>card.classList.remove(c));
        card.classList.add(room.estado.toLowerCase());card.querySelector('.room-state').textContent=room.etiqueta;
      });
      document.getElementById('ultima-actualizacion').textContent=`Actualizado ${new Date(data.actualizado).toLocaleTimeString('es-CL')}`;
    }catch{const label=document.getElementById('ultima-actualizacion');if(label)label.textContent='Sin conexión. Se reintentará automáticamente.';}
    finally{esperando=false;}
  }
  if(dashboard){const timer=setInterval(actualizar,5000);window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});}
});
