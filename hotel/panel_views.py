import uuid
from datetime import date
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import Count, Max, Q, Value
from django.db.models.functions import Replace, Upper
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .comprobantes import extras_facturados, enviar_confirmacion_reserva
from .models import BitacoraReserva, CampanaPromocion, Cliente, EnvioPromocion, Habitacion, Reserva, ServicioAdicional, SolicitudExtra
from .personal import acceso_personal, pagar_saldo_estadia, agregar_extra_estadia, cancelar_por_personal, cambiar_disponibilidad_extra, cambiar_estado_habitacion, datos_dashboard, modificar_por_personal, registrar_checkin, registrar_checkout, resolver_extra_estadia
from .promociones import crear_campana, enviar_campana


def contexto_panel(request, seccion):
    return {'personal': request.personal_hotel, 'es_administrador': request.es_administrador, 'seccion': seccion}


def fecha_consulta(request):
    try:
        return date.fromisoformat(request.GET.get('fecha', ''))
    except ValueError:
        return timezone.localdate()


@never_cache
@acceso_personal()
def dashboard(request):
    datos = datos_dashboard(fecha_consulta(request))
    contexto = contexto_panel(request, 'dashboard')
    contexto.update({'datos': datos, 'fecha': date.fromisoformat(datos['fecha']), 'hoy': timezone.localdate()})
    return render(request, 'hotel/panel/dashboard.html', contexto)


@never_cache
@acceso_personal()
def estado_dashboard(request):
    return JsonResponse(datos_dashboard(fecha_consulta(request)))


@never_cache
@acceso_personal()
def reservas(request):
    q = request.GET.get('q', '').strip()
    listado = Reserva.objects.select_related('habitacion', 'cliente__usuario').annotate(
        extras_pendientes=Count('solicitudes_extra', filter=Q(solicitudes_extra__estado=SolicitudExtra.Estado.PENDIENTE)),
    )
    if q:
        rut = q.replace('.', '').replace(' ', '').upper()
        listado = listado.annotate(rut_busqueda=Replace(Upper('titular_rut'), Value('.'), Value(''))).filter(
            Q(codigo__icontains=q) | Q(titular_nombre__icontains=q) | Q(titular_apellido__icontains=q) | Q(rut_busqueda__icontains=rut)
        )
    estado = request.GET.get('estado', '')
    if estado in Reserva.Estado.values:
        listado = listado.filter(estado=estado)
    if request.GET.get('llegadas') == 'hoy':
        listado = listado.filter(fecha_entrada__date=timezone.localdate(), estado='CONFIRMADA')
    contexto = contexto_panel(request, 'reservas')
    contexto.update({'pagina': Paginator(listado.order_by('-fecha_creacion'), 25).get_page(request.GET.get('page')), 'q': q, 'estado': estado, 'estados': Reserva.Estado.choices, 'hoy': timezone.localdate()})
    return render(request, 'hotel/panel/reservas.html', contexto)


class ModificarReservaForm(forms.Form):
    habitacion = forms.ModelChoiceField(queryset=Habitacion.objects.all(), label='Habitación')
    fecha_entrada = forms.DateTimeField(label='Entrada', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'), input_formats=['%Y-%m-%dT%H:%M'])
    fecha_salida = forms.DateTimeField(label='Salida', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'), input_formats=['%Y-%m-%dT%H:%M'])
    numero_huespedes = forms.IntegerField(label='Huéspedes', min_value=1, max_value=6)
    motivo = forms.CharField(label='Motivo de modificación', max_length=1000, widget=forms.Textarea(attrs={'rows': 3}))


class ExtraEstadiaForm(forms.Form):
    servicio = forms.ModelChoiceField(queryset=ServicioAdicional.objects.none(), label='Extra')
    cantidad = forms.IntegerField(label='Unidades a agregar', min_value=1, max_value=99, initial=1)
    solicitud = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, reserva, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['servicio'].queryset = ServicioAdicional.disponibles_para(reserva.habitacion.tipo).order_by('nombre')


class PagoSaldoForm(forms.Form):
    importe = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)
    confirmar = forms.BooleanField()
    revision = forms.CharField()


class ResolverExtraForm(forms.Form):
    solicitud_extra = forms.IntegerField(min_value=1)
    decision = forms.ChoiceField(choices=(('confirmar', 'Confirmar'), ('rechazar', 'Rechazar')))
    respuesta = forms.CharField(max_length=250, required=False)


@never_cache
@acceso_personal()
def detalle_reserva(request, pk):
    reserva = get_object_or_404(Reserva.objects.select_related('habitacion', 'cliente__usuario'), pk=pk)
    contexto = contexto_panel(request, 'reservas')
    contexto.update({'reserva': reserva, 'form': ModificarReservaForm(initial={campo: getattr(reserva, campo) for campo in ('habitacion', 'fecha_entrada', 'fecha_salida', 'numero_huespedes')}), 'hoy': timezone.localdate(), 'eventos': reserva.eventos.select_related('personal__usuario')[:10]})
    contexto['revision_saldo'] = signing.dumps([reserva.pk, str(reserva.monto_total), str(reserva.monto_pagado_adicional)], salt='hotel.saldo')
    contexto['extras'] = extras_facturados(reserva)
    contexto['solicitudes_extra'] = reserva.solicitudes_extra.select_related('servicio')
    if reserva.estado == Reserva.Estado.EN_ESTADIA:
        contexto['extra_form'] = ExtraEstadiaForm(reserva=reserva, initial={'solicitud': uuid.uuid4()})
    return render(request, 'hotel/panel/reserva.html', contexto)


@require_POST
@acceso_personal()
def accion_reserva(request, pk):
    reserva = get_object_or_404(Reserva.objects.select_related('habitacion'), pk=pk)
    accion = request.POST.get('accion')
    try:
        if accion == 'checkin':
            _, cambio = registrar_checkin(pk, request.personal_hotel)
            mensaje = 'Check-in registrado. La habitación está ocupada.' if cambio else 'El check-in ya estaba registrado.'
        elif accion == 'agregar_extra':
            extra_form = ExtraEstadiaForm(request.POST, reserva=reserva)
            if not extra_form.is_valid():
                raise ValidationError('Selecciona un servicio disponible e ingresa entre 1 y 99 unidades.')
            cambio = agregar_extra_estadia(pk, request.personal_hotel, extra_form.cleaned_data)
            mensaje = 'Extra agregado al total de la estadía.' if cambio else 'Este extra ya fue registrado.'
        elif accion == 'resolver_extra':
            respuesta = ResolverExtraForm(request.POST)
            if not respuesta.is_valid():
                raise ValidationError('Revisa la solicitud y el mensaje para el huésped.')
            cambio = resolver_extra_estadia(pk, request.personal_hotel, **{
                'solicitud_id': respuesta.cleaned_data['solicitud_extra'],
                'decision': respuesta.cleaned_data['decision'],
                'respuesta': respuesta.cleaned_data['respuesta'],
            })
            mensaje = 'Solicitud actualizada.' if cambio else 'La solicitud ya fue atendida.'
        elif accion == 'pagar_saldo':
            form_pago = PagoSaldoForm(request.POST)
            if not form_pago.is_valid():
                raise ValidationError('Confirma el pago simulado del saldo mostrado.')
            cambio = pagar_saldo_estadia(pk, request.personal_hotel, form_pago.cleaned_data['importe'], form_pago.cleaned_data['revision'])
            mensaje = 'Pago adicional simulado registrado.' if cambio else 'La reserva ya está pagada.'
        elif accion == 'correo_confirmacion':
            enviado = enviar_confirmacion_reserva(reserva, request.personal_hotel)
            reserva.refresh_from_db()
            if not enviado:
                raise ValidationError(f'Correo: {reserva.get_correo_estado_display()}. {reserva.correo_error}')
            mensaje = 'Correo de confirmación enviado.'
        elif accion == 'checkout':
            _, cambio = registrar_checkout(pk, request.personal_hotel)
            mensaje = 'Salida registrada. La estadía está completada.' if cambio else 'La salida ya estaba registrada.'
        elif accion == 'cancelar':
            _, cambio = cancelar_por_personal(pk, request.personal_hotel, request.POST.get('motivo', ''))
            mensaje = 'Reserva cancelada y motivo registrado en auditoría.' if cambio else 'La reserva ya estaba cancelada.'
        elif accion == 'modificar':
            form = ModificarReservaForm(request.POST)
            if not form.is_valid():
                raise ValidationError('Revisa los campos de modificación: ' + '; '.join(f'{k}: {", ".join(v)}' for k,v in form.errors.items()))
            modificar_por_personal(pk, request.personal_hotel, form.cleaned_data)
            mensaje = 'Reserva modificada y cambios registrados en auditoría.'
        else:
            raise ValidationError('Acción no válida.')
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    except OperationalError:
        messages.error(request, 'La reserva está siendo actualizada. Vuelve a intentarlo.')
    else:
        messages.success(request, mensaje)
    return redirect('hotel:panel_reserva', pk=pk)


@never_cache
@acceso_personal()
def disponibilidad_extras(request):
    contexto = contexto_panel(request, 'extras')
    contexto['servicios'] = ServicioAdicional.objects.order_by('categoria', 'nombre')
    return render(request, 'hotel/panel/extras.html', contexto)


@require_POST
@acceso_personal()
def estado_extra(request, pk):
    get_object_or_404(ServicioAdicional, pk=pk)
    estado = request.POST.get('activo')
    if estado not in ('1', '0'):
        messages.error(request, 'Elige un estado válido.')
    else:
        try:
            cambiar_disponibilidad_extra(pk, request.personal_hotel, estado == '1')
        except OperationalError:
            messages.error(request, 'El catálogo está siendo actualizado. Inténtalo nuevamente.')
        else:
            messages.success(request, 'Disponibilidad actualizada.')
    return redirect('hotel:panel_extras')


@require_POST
@acceso_personal(solo_admin=True)
def estado_habitacion(request, pk):
    get_object_or_404(Habitacion, pk=pk)
    try:
        cambiar_estado_habitacion(pk, request.personal_hotel, request.POST.get('estado'), request.POST.get('motivo', ''))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Estado de habitación actualizado y auditado.')
    return redirect('hotel:panel_dashboard')


def clientes_frecuentes():
    return Cliente.objects.select_related('usuario').annotate(
        estadias=Count('reservas', filter=Q(reservas__estado='COMPLETADA')),
        reservas_total=Count('reservas', filter=~Q(reservas__estado='RETENIDA')),
        ultima_visita=Max('reservas__fecha_checkin'),
    ).order_by('-estadias', '-reservas_total', 'usuario__apellido', 'usuario__nombre')


@never_cache
@acceso_personal(solo_admin=True)
def clientes(request):
    listado = clientes_frecuentes()
    q = request.GET.get('q', '').strip()
    if q:
        listado = listado.filter(Q(usuario__nombre__icontains=q) | Q(usuario__apellido__icontains=q) | Q(usuario__email__icontains=q) | Q(usuario__rut__icontains=q.replace('.', '').replace(' ', '')))
    contexto = contexto_panel(request, 'clientes')
    contexto.update({'pagina': Paginator(listado,25).get_page(request.GET.get('page')), 'q': q})
    return render(request, 'hotel/panel/clientes.html', contexto)


class DestinatariosPromocionField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, cliente):
        return f'{cliente.usuario.get_full_name()} · RUT {cliente.usuario.rut}'


class PromocionForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    asunto = forms.CharField(max_length=180, label='Asunto del correo')
    mensaje = forms.CharField(label='Mensaje', widget=forms.Textarea(attrs={'rows': 4}), max_length=5000)
    porcentaje = forms.DecimalField(label='Descuento en alojamiento (%)', min_value=Decimal('0.01'), max_value=100, max_digits=5, decimal_places=2)
    fecha_vencimiento = forms.DateField(label='Vencimiento del cupón', widget=forms.DateInput(attrs={'type': 'date'}))
    clientes = DestinatariosPromocionField(label='Destinatarios', queryset=Cliente.objects.filter(usuario__is_active=True).select_related('usuario'), widget=forms.CheckboxSelectMultiple)

    def clean_fecha_vencimiento(self):
        valor = self.cleaned_data['fecha_vencimiento']
        if valor < timezone.localdate():
            raise forms.ValidationError('Selecciona hoy o una fecha posterior.')
        return valor


@never_cache
@acceso_personal(solo_admin=True)
def promociones(request):
    form = PromocionForm(request.POST if request.method == 'POST' else None, initial={'token': uuid.uuid4(), 'porcentaje': 10})
    if request.method == 'POST' and form.is_valid():
        try:
            campana, creada = crear_campana(request.personal_hotel, form.cleaned_data)
            if creada:
                enviar_campana(campana, request.personal_hotel)
        except ValidationError as error:
            form.add_error(None, ' '.join(error.messages))
        else:
            messages.success(request, 'Campaña procesada. Revisa el resultado de cada envío.' if creada else 'La campaña ya había sido procesada; no se duplicaron cupones ni envíos.')
            return redirect('hotel:panel_campana', pk=campana.pk)
    contexto = contexto_panel(request, 'promociones')
    contexto.update({'form': form, 'campanas': CampanaPromocion.objects.annotate(total=Count('envios'), enviados=Count('envios', filter=Q(envios__estado='ENVIADO'))).select_related('personal__usuario')[:20], 'correo_local': settings.EMAIL_BACKEND.endswith('filebased.EmailBackend')})
    return render(request, 'hotel/panel/promociones.html', contexto)


@never_cache
@acceso_personal(solo_admin=True)
def campana(request, pk):
    contexto = contexto_panel(request, 'promociones')
    contexto.update({'campana': get_object_or_404(CampanaPromocion,pk=pk), 'envios': EnvioPromocion.objects.filter(campana_id=pk).select_related('cliente__usuario', 'cupon'), 'correo_local': settings.EMAIL_BACKEND.endswith('filebased.EmailBackend')})
    return render(request, 'hotel/panel/campana.html', contexto)


@require_POST
@acceso_personal(solo_admin=True)
def reintentar_campana(request, pk):
    objeto = get_object_or_404(CampanaPromocion,pk=pk)
    pendientes = request.POST.get('grupo') == 'pendientes'
    enviar_campana(objeto, request.personal_hotel, reintentar=not pendientes)
    messages.info(request, 'Se procesaron los envíos pendientes.' if pendientes else 'Se reintentaron los envíos fallidos.')
    return redirect('hotel:panel_campana', pk=pk)


@never_cache
@acceso_personal(solo_admin=True)
def correo_promocion(request, pk):
    envio = get_object_or_404(EnvioPromocion.objects.select_related('campana'),pk=pk)
    return HttpResponse(f'Para: {envio.destinatario}\nAsunto: {envio.campana.asunto}\nEstado: {envio.get_estado_display()}\n\n{envio.cuerpo}', content_type='text/plain; charset=utf-8')


@never_cache
@acceso_personal(solo_admin=True)
def auditoria(request):
    eventos = BitacoraReserva.objects.select_related('personal__usuario', 'reserva', 'habitacion', 'cliente__usuario')
    q = request.GET.get('q','').strip()
    if q:
        eventos = eventos.filter(Q(reserva__codigo__icontains=q) | Q(motivo__icontains=q) | Q(personal__usuario__nombre__icontains=q) | Q(personal__usuario__apellido__icontains=q) | Q(accion__icontains=q))
    contexto = contexto_panel(request, 'auditoria')
    contexto.update({'pagina': Paginator(eventos,25).get_page(request.GET.get('page')), 'q':q})
    return render(request, 'hotel/panel/auditoria.html', contexto)
