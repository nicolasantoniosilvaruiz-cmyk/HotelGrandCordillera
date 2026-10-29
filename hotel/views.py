from datetime import date, timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Cliente, Habitacion, Reserva, ServicioAdicional, Usuario
from .reservas import liberar_retencion


class BusquedaDisponibilidadForm(forms.Form):
    entrada = forms.DateField()
    salida = forms.DateField()
    huespedes = forms.IntegerField(min_value=1, max_value=6, initial=2)

    def clean(self):
        datos = super().clean()
        entrada, salida = datos.get('entrada'), datos.get('salida')
        if entrada and entrada < timezone.localdate():
            self.add_error('entrada', 'Selecciona una fecha desde hoy.')
        if entrada and salida and salida <= entrada:
            self.add_error('salida', 'La salida debe ser posterior a la entrada.')
        return datos


def home(request):
    hoy = timezone.localdate()
    entrada = hoy + timedelta(days=1)
    salida = entrada + timedelta(days=2)
    busqueda = None
    habitaciones = Habitacion.objects.exclude(estado_actual=Habitacion.Estado.MANTENIMIENTO)
    busqueda_valida = False
    if request.GET:
        busqueda = BusquedaDisponibilidadForm(request.GET)
        if busqueda.is_valid():
            busqueda_valida = True
            entrada = busqueda.cleaned_data['entrada']
            salida = busqueda.cleaned_data['salida']
            bloqueos = Reserva.objects.filter(
                fecha_entrada__date__lt=salida,
                fecha_salida__date__gt=entrada,
            ).filter(
                Q(estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.EN_ESTADIA])
                | Q(estado=Reserva.Estado.RETENIDA, retencion_hasta__gt=timezone.now())
            )
            habitaciones = habitaciones.exclude(pk__in=bloqueos.values('habitacion_id'))
    categorias = []
    descripciones = {
        'NORMAL': 'Un espacio acogedor para hacer una pausa y disfrutar del sur.',
        'MEDIANA': 'Más espacio para descansar a tu ritmo y compartir la escapada.',
        'GRANDE': 'La comodidad de una suite para una estadía especial.',
    }
    for tipo, nombre in Habitacion.Tipo.choices:
        opciones = habitaciones.filter(tipo=tipo).order_by('precio_base_noche')
        primera = opciones.first()
        if primera:
            categorias.append({
                'nombre': nombre, 'tipo': tipo, 'descripcion': descripciones[tipo],
                'precio': primera.precio_base_noche, 'cantidad': opciones.count(),
            })
    return render(request, 'hotel/home.html', {
        'hoy': hoy, 'entrada': entrada, 'salida': salida,
        'huespedes': busqueda.cleaned_data.get('huespedes', 2) if busqueda and busqueda.is_valid() else 2,
        'busqueda': busqueda, 'busqueda_valida': busqueda_valida,
        'categorias': categorias,
        'servicios': ServicioAdicional.objects.filter(activo=True),
    })


class RegistroForm(UserCreationForm):
    acepta_condiciones = forms.BooleanField(label='Acepto los términos y condiciones y la política de privacidad.')

    class Meta:
        model = Usuario
        fields = ('nombre', 'apellido', 'rut', 'email', 'telefono')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if Usuario.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ya existe una cuenta con este correo.')
        return email

    def clean_rut(self):
        rut = self.cleaned_data['rut'].strip().replace('.', '').replace(' ', '').upper()
        if Usuario.objects.filter(rut=rut).exists():
            raise forms.ValidationError('Ya existe una cuenta con este RUT.')
        return rut


def registro(request):
    siguiente = request.POST.get('next') or request.GET.get('next') or reverse('hotel:fechas')
    if not url_has_allowed_host_and_scheme(siguiente, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        siguiente = reverse('hotel:fechas')
    if request.user.is_authenticated:
        return redirect(siguiente)
    form = RegistroForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            usuario = form.save()
            Cliente.objects.create(usuario=usuario)
        login(request, usuario)
        messages.success(request, 'Tu cuenta está lista. Bienvenido a Grand Cordillera.')
        return redirect(siguiente)
    return render(request, 'hotel/cuenta.html', {
        'form': form, 'es_registro': True, 'next': siguiente, 'paso': 3,
        'puede_invitado': bool(request.session.get('seleccion_estadia')),
    })


def habitaciones_disponibles(entrada, salida, reserva_propia=None):
    bloqueos = Reserva.objects.filter(
        fecha_entrada__date__lt=salida, fecha_salida__date__gt=entrada,
    ).filter(
        Q(estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.EN_ESTADIA])
        | Q(estado=Reserva.Estado.RETENIDA, retencion_hasta__gt=timezone.now())
    )
    if reserva_propia:
        bloqueos = bloqueos.exclude(pk=reserva_propia)
    return Habitacion.objects.exclude(
        estado_actual=Habitacion.Estado.MANTENIMIENTO,
    ).exclude(pk__in=bloqueos.values('habitacion_id')).order_by('tipo', 'numero')


def fechas(request):
    hoy = timezone.localdate()
    borrador = request.session.get('seleccion_estadia', {})
    inicial = {
        'entrada': date.fromisoformat(borrador.get('entrada', (hoy + timedelta(days=1)).isoformat())),
        'salida': date.fromisoformat(borrador.get('salida', (hoy + timedelta(days=3)).isoformat())),
        'huespedes': borrador.get('huespedes', 2),
    }
    form = BusquedaDisponibilidadForm(request.GET or None, initial=inicial)
    errores = None
    if request.GET:
        if form.is_valid():
            inicial = form.cleaned_data
        else:
            errores = form.errors
    return render(request, 'hotel/fechas.html', {
        'paso': 1, 'hoy': hoy, 'datos': inicial, 'errores': errores,
    })


def habitaciones(request):
    form = BusquedaDisponibilidadForm(request.GET)
    if not form.is_valid():
        return redirect(reverse('hotel:fechas') + '?' + request.GET.urlencode())
    datos = form.cleaned_data
    opciones = list(habitaciones_disponibles(datos['entrada'], datos['salida'], request.session.get('reserva_en_curso')))
    orden_tipos = {tipo: indice for indice, (tipo, _) in enumerate(Habitacion.Tipo.choices)}
    opciones.sort(key=lambda h: (orden_tipos[h.tipo], h.numero))
    borrador = request.session.get('seleccion_estadia', {})
    mismo_rango = (
        borrador.get('entrada') == datos['entrada'].isoformat()
        and borrador.get('salida') == datos['salida'].isoformat()
        and borrador.get('huespedes') == datos['huespedes']
    )
    seleccionado = next((h for h in opciones if mismo_rango and h.pk == borrador.get('habitacion')), None)
    return render(request, 'hotel/habitaciones.html', {
        'paso': 2, 'datos': datos, 'noches': (datos['salida'] - datos['entrada']).days,
        'habitaciones': opciones, 'seleccionada': seleccionado or (opciones[0] if opciones else None),
        'servicios': ServicioAdicional.objects.filter(activo=True),
        'extras_guardados': borrador.get('extras', {}) if mismo_rango else {},
        'query': request.GET.urlencode(),
    })


@require_POST
def seleccionar_estadia(request):
    form = BusquedaDisponibilidadForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Revisa las fechas de tu estadía antes de continuar.')
        return redirect('hotel:fechas')
    datos = form.cleaned_data
    identificador = request.POST.get('habitacion', '')
    habitacion = habitaciones_disponibles(datos['entrada'], datos['salida'], request.session.get('reserva_en_curso')).filter(pk=identificador if identificador.isdigit() else None).first()
    consulta = urlencode({k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in datos.items()})
    if habitacion is None:
        messages.error(request, 'Esa habitación ya no está disponible. Elige otra opción.')
        return redirect(reverse('hotel:habitaciones') + '?' + consulta)
    extras = {}
    for servicio in ServicioAdicional.objects.filter(activo=True):
        cantidad = request.POST.get(f'extra_{servicio.pk}', '0')
        if not cantidad.isdigit() or not 0 <= int(cantidad) <= 99:
            messages.error(request, 'La cantidad de cada extra debe estar entre 0 y 99.')
            return redirect(reverse('hotel:habitaciones') + '?' + consulta)
        if int(cantidad):
            extras[str(servicio.pk)] = int(cantidad)
    seleccion = {
        'entrada': datos['entrada'].isoformat(), 'salida': datos['salida'].isoformat(),
        'huespedes': datos['huespedes'], 'habitacion': habitacion.pk, 'extras': extras,
    }
    if seleccion != request.session.get('reserva_seleccion'):
        if request.session.get('reserva_en_curso'):
            liberar_retencion(request.session['reserva_en_curso'])
        request.session.pop('reserva_en_curso', None)
    request.session['seleccion_estadia'] = seleccion
    destino = reverse('hotel:pago')
    if not request.user.is_authenticated:
        return redirect(reverse('hotel:registro') + '?' + urlencode({'next': destino}))
    return redirect(destino)
