document.addEventListener('DOMContentLoaded',()=>{
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
