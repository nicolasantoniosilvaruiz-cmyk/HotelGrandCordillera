document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-password]').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.password);
    const mostrar = input.type === 'password';
    input.type = mostrar ? 'text' : 'password';
    button.textContent = mostrar ? 'Ocultar' : 'Mostrar';
    button.setAttribute('aria-label', `${mostrar ? 'Ocultar' : 'Mostrar'} contraseña`);
  }));

  const form = document.getElementById('form-fechas');
  if (form) {
    const entrada = form.querySelector('[name=entrada]'), salida = form.querySelector('[name=salida]');
    const huespedes = form.querySelector('[name=huespedes]');
    const root = document.getElementById('calendarios');
    const iso = fecha => `${fecha.getFullYear()}-${String(fecha.getMonth()+1).padStart(2,'0')}-${String(fecha.getDate()).padStart(2,'0')}`;
    const parse = valor => new Date(`${valor}T12:00:00`);
    const dias = (a,b) => Math.round((Date.UTC(...b.split('-').map((n,i)=>Number(n)-(i===1?1:0)))-Date.UTC(...a.split('-').map((n,i)=>Number(n)-(i===1?1:0))))/86400000);
    const corto = valor => new Intl.DateTimeFormat('es-CL',{day:'numeric',month:'short'}).format(parse(valor));
    const hoy = form.dataset.today;
    let mes = parse(entrada.value || hoy); mes.setDate(1);
    let eligiendoSalida = false;
    const actualizar = () => {
      root.replaceChildren();
      const e = entrada.value, s = salida.value;
      const completo = e && s && s > e;
      document.getElementById('rango-calendario').textContent = completo ? `${corto(e)} → ${corto(s)} · ${dias(e,s)} noches` : 'Selecciona entrada y salida';
      document.getElementById('detalle-fechas').textContent = completo ? `${corto(e)} → ${corto(s)} · ${dias(e,s)} noches\n${huespedes.value} adultos · 1 habitación` : 'Primero selecciona tu entrada y luego tu salida.';
      for (let i=0;i<2;i++) {
        const fechaMes = new Date(mes.getFullYear(),mes.getMonth()+i,1,12);
        const section = document.createElement('section'); section.className='calendar-month';
        const h2 = document.createElement('h2'); h2.textContent = new Intl.DateTimeFormat('es-CL',{month:'long',year:'numeric'}).format(fechaMes); section.append(h2);
        const week = document.createElement('div'); week.className='calendar-week'; ['L','M','M','J','V','S','D'].forEach(d=>{const span=document.createElement('span');span.textContent=d;week.append(span);});section.append(week);
        const days = document.createElement('div');days.className='calendar-days';
        for(let j=0;j<(fechaMes.getDay()+6)%7;j++) days.append(document.createElement('span'));
        const max = new Date(fechaMes.getFullYear(),fechaMes.getMonth()+1,0).getDate();
        for(let d=1;d<=max;d++) {
          const fecha = new Date(fechaMes.getFullYear(),fechaMes.getMonth(),d,12), valor = iso(fecha);
          const button = document.createElement('button');button.type='button';button.className='calendar-day';button.textContent=d;button.disabled=valor<hoy;
          button.setAttribute('aria-label',new Intl.DateTimeFormat('es-CL',{day:'numeric',month:'long',year:'numeric'}).format(fecha));
          if(valor===hoy)button.classList.add('today');
          if(valor===e||valor===s)button.classList.add('endpoint');
          if(completo&&valor>e&&valor<s)button.classList.add('in-range');
          button.setAttribute('aria-pressed',String(valor===e||valor===s));
          button.addEventListener('click',()=>{
            if(!eligiendoSalida||!entrada.value||valor<=entrada.value){entrada.value=valor;salida.value='';eligiendoSalida=true;}
            else{salida.value=valor;eligiendoSalida=false;}
            salida.min=entrada.value;actualizar();
          });days.append(button);
        }
        section.append(days);root.append(section);
      }
      document.getElementById('mes-anterior').disabled = mes.getFullYear()*12+mes.getMonth() <= parse(hoy).getFullYear()*12+parse(hoy).getMonth();
    };
    document.getElementById('mes-anterior').addEventListener('click',()=>{mes.setMonth(mes.getMonth()-1);actualizar();});
    document.getElementById('mes-siguiente').addEventListener('click',()=>{mes.setMonth(mes.getMonth()+1);actualizar();});
    document.getElementById('limpiar-fechas').addEventListener('click',()=>{entrada.value='';salida.value='';eligiendoSalida=false;actualizar();});
    [entrada,salida,huespedes].forEach(input=>input.addEventListener('change',()=>{if(entrada.value){mes=parse(entrada.value);mes.setDate(1);}actualizar();}));
    actualizar();
  }

  const seleccion = document.getElementById('seleccion-habitacion');
  if (seleccion) {
    const moneda = centavos => new Intl.NumberFormat('es-CL',{style:'currency',currency:'CLP',maximumFractionDigits:2}).format(centavos/100);
    const centavos = valor => Math.round(Number(valor)*100);
    const guardados = JSON.parse(document.getElementById('extras-guardados').textContent);
    seleccion.querySelectorAll('[data-extra]').forEach(input=>{input.value=guardados[input.dataset.id]||0;});
    const actualizar = () => {
      const elegido = seleccion.querySelector('[name=habitacion]:checked'); if(!elegido)return;
      seleccion.querySelectorAll('.room-option').forEach(card=>{const active=card.contains(elegido);card.classList.toggle('selected',active);card.querySelector('.select-label').textContent=active?'✓ Seleccionada':'Seleccionar';});
      document.getElementById('resumen-habitacion').textContent=elegido.dataset.nombre;
      let total=centavos(elegido.dataset.precio)*Number(seleccion.dataset.noches);
      document.getElementById('resumen-alojamiento').textContent=moneda(total);
      const extras=document.getElementById('resumen-extras');extras.replaceChildren();
      seleccion.querySelectorAll('[data-extra]').forEach(input=>{
        const cantidad=Number(input.value)||0; input.closest('.extra-option').classList.toggle('active',cantidad>0);
        if(!Number.isInteger(cantidad)||cantidad<0||cantidad>99)return;
        if(cantidad){const subtotal=cantidad*centavos(input.dataset.precio);total+=subtotal;const line=document.createElement('div');const nombre=document.createElement('span'),precio=document.createElement('strong');nombre.textContent=`${input.dataset.nombre} × ${cantidad}`;precio.textContent=moneda(subtotal);line.append(nombre,precio);extras.append(line);}
      });document.getElementById('resumen-total').textContent=moneda(total);
    };
    seleccion.addEventListener('input',actualizar);seleccion.addEventListener('change',actualizar);actualizar();
  }
});
