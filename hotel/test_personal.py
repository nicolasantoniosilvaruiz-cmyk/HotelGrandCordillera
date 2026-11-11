import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import BitacoraReserva, CampanaPromocion, Cliente, CuponDescuento, EnvioPromocion, Habitacion, PersonalHotel, Reserva, ServicioAdicional, SolicitudExtra, Usuario
from .panel_views import clientes_frecuentes
from .personal import pagar_saldo_estadia, cancelar_por_personal, cambiar_estado_habitacion, datos_dashboard, modificar_por_personal, registrar_checkin, registrar_checkout
from .promociones import crear_campana, enviar_campana
from .reservas import ErrorReserva, aplicar_cupon_reserva, confirmar_pago, preparar_pago, revision_pago


class PersonalTests(TestCase):
    def setUp(self):
        def usuario(email,nombre,rut):
            return Usuario.objects.create_user(email=email,nombre=nombre,apellido='Prueba',rut=rut,telefono='912345678')
        self.admin = usuario('admin@example.com','Admin','11111111-1')
        self.administrador = PersonalHotel.objects.create(usuario=self.admin,codigo_empleado='ADM-1',rol='ADMINISTRADOR')
        self.recepcionista_user = usuario('recepcion@example.com','Recepcion','22222222-2')
        self.recepcionista = PersonalHotel.objects.create(usuario=self.recepcionista_user,codigo_empleado='REC-1',rol='RECEPCIONISTA')
        self.usuario = usuario('cliente@example.com','Ana','33333333-3')
        self.cliente_hotel = Cliente.objects.create(usuario=self.usuario)
        self.habitacion = Habitacion.objects.create(numero=101,tipo='NORMAL',precio_base_noche=Decimal('20000'))
        self.entrada = timezone.make_aware(datetime.combine(timezone.localdate(),time(15)))

    def reserva(self,**extras):
        datos = dict(pago_simulado=True,habitacion=self.habitacion,cliente=self.cliente_hotel,fecha_entrada=self.entrada,fecha_salida=self.entrada+timedelta(days=2),estado='CONFIRMADA',fecha_confirmacion=timezone.now(),numero_huespedes=2)
        datos.update(extras)
        return Reserva.objects.create(**datos)

    def seleccion(self):
        return {'habitacion':self.habitacion.pk,'entrada':(timezone.localdate()+timedelta(days=10)).isoformat(),'salida':(timezone.localdate()+timedelta(days=12)).isoformat(),'huespedes':2,'extras':{}}

    def campana_datos(self):
        return {'token':uuid.uuid4(),'asunto':'Vuelve al sur','mensaje':'Te esperamos en tu próxima escapada.','porcentaje':Decimal('10'),'fecha_vencimiento':timezone.localdate()+timedelta(days=30),'clientes':[self.cliente_hotel]}

    def datos_pago(self):
        return {'resultado_pago':'APROBADO','titular_nombre':'Ana','titular_apellido':'Prueba','titular_rut':'33333333-3','titular_correo':'cliente@example.com','titular_telefono':'912345678'}

    def test_permisos_y_todas_las_pantallas(self):
        self.assertEqual(self.client.get(reverse('hotel:panel_dashboard')).status_code,302)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse('hotel:panel_dashboard')).status_code,403)
        self.client.force_login(self.recepcionista_user)
        self.assertEqual(self.client.get(reverse('hotel:panel_dashboard')).status_code,200)
        self.assertEqual(self.client.get(reverse('hotel:panel_reservas')).status_code,200)
        for nombre in ('panel_clientes','panel_promociones','panel_auditoria'):
            self.assertEqual(self.client.get(reverse('hotel:'+nombre)).status_code,403)
        self.client.force_login(self.admin)
        for nombre in ('panel_dashboard','panel_reservas','panel_clientes','panel_promociones','panel_auditoria','panel_estado'):
            self.assertEqual(self.client.get(reverse('hotel:'+nombre)).status_code,200)

    def test_superusuario_accede_y_obtiene_perfil_auditable(self):
        root = Usuario.objects.create_superuser(email='root@example.com',nombre='Root',apellido='Hotel',rut='44444444-4',telefono='912345678')
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse('hotel:panel_dashboard')).status_code,200)
        self.assertEqual(root.personal_hotel.rol,'ADMINISTRADOR')

    def test_is_staff_sin_rol_no_da_acceso_y_un_cliente_no_ejecuta_acciones(self):
        self.usuario.is_staff=True;self.usuario.save(update_fields=['is_staff'])
        self.client.force_login(self.usuario)
        reserva=self.reserva()
        self.assertEqual(self.client.get(reverse('hotel:panel_dashboard')).status_code,403)
        self.assertEqual(self.client.post(reverse('hotel:panel_accion',args=[reserva.pk]),{'accion':'checkin'}).status_code,403)
        reserva.refresh_from_db();self.assertEqual(reserva.estado,'CONFIRMADA')

    def test_panel_cambio_requiere_motivo_y_metodo_post(self):
        self.client.force_login(self.recepcionista_user)
        reserva=self.reserva()
        url=reverse('hotel:panel_accion',args=[reserva.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        self.client.post(url,{'accion':'cancelar','motivo':''})
        reserva.refresh_from_db();self.assertEqual(reserva.estado,'CONFIRMADA')
        self.assertFalse(BitacoraReserva.objects.exists())

    def test_checkin_y_checkout_actualizan_estado_hora_y_bitacora_una_vez(self):
        reserva = self.reserva()
        _,cambio = registrar_checkin(reserva.pk,self.recepcionista)
        self.assertTrue(cambio)
        reserva.refresh_from_db();self.habitacion.refresh_from_db()
        self.assertEqual(reserva.estado,'EN_ESTADIA');self.assertIsNotNone(reserva.fecha_checkin)
        self.assertEqual(self.habitacion.estado_actual,'OCUPADA')
        self.assertFalse(registrar_checkin(reserva.pk,self.recepcionista)[1])
        self.assertEqual(BitacoraReserva.objects.filter(accion='CHECK_IN').count(),1)
        registrar_checkout(reserva.pk,self.recepcionista)
        reserva.refresh_from_db();self.habitacion.refresh_from_db()
        self.assertEqual(reserva.estado,'COMPLETADA');self.assertIsNotNone(reserva.fecha_checkout)
        self.assertEqual(self.habitacion.estado_actual,'DISPONIBLE')
        self.assertFalse(registrar_checkout(reserva.pk,self.recepcionista)[1])
        self.assertEqual(BitacoraReserva.objects.count(),2)
        evento=BitacoraReserva.objects.get(accion='CHECK_IN')
        self.assertEqual(evento.personal,self.recepcionista)
        self.assertEqual(evento.datos_antes['estado'],'CONFIRMADA')
        self.assertEqual(evento.datos_despues['estado'],'EN_ESTADIA')

    def test_checkin_rechaza_otro_dia_y_habitacion_ocupada_o_mantenimiento(self):
        reserva=self.reserva(fecha_entrada=self.entrada+timedelta(days=1))
        with self.assertRaises(ValidationError):registrar_checkin(reserva.pk,self.recepcionista)
        reserva.fecha_entrada=self.entrada;reserva.save(update_fields=['fecha_entrada'])
        for estado in ('OCUPADA','MANTENIMIENTO'):
            self.habitacion.estado_actual=estado;self.habitacion.save()
            with self.assertRaises(ValidationError):registrar_checkin(reserva.pk,self.recepcionista)
        self.assertFalse(BitacoraReserva.objects.exists())

    def test_fallo_auditoria_revierte_checkin_completo(self):
        reserva=self.reserva()
        with patch('hotel.personal.auditar',side_effect=RuntimeError('fallo de prueba')):
            with self.assertRaises(RuntimeError):registrar_checkin(reserva.pk,self.recepcionista)
        reserva.refresh_from_db();self.habitacion.refresh_from_db()
        self.assertEqual(reserva.estado,'CONFIRMADA');self.assertEqual(self.habitacion.estado_actual,'DISPONIBLE')

    def test_cancelacion_administrativa_sin_limite_exige_motivo(self):
        reserva=self.reserva(fecha_entrada=timezone.now()+timedelta(hours=1))
        with self.assertRaises(ValidationError):cancelar_por_personal(reserva.pk,self.recepcionista,' ')
        cancelar_por_personal(reserva.pk,self.recepcionista,'Solicitado presencialmente por el huésped')
        self.assertFalse(cancelar_por_personal(reserva.pk,self.recepcionista,'Repetición')[1])
        reserva.refresh_from_db();self.assertEqual(reserva.estado,'CANCELADA')
        self.assertEqual(BitacoraReserva.objects.get().motivo,'Solicitado presencialmente por el huésped')

    def test_modificacion_valida_y_rechazo_de_solapamientos(self):
        self.habitacion.tipo = Habitacion.Tipo.MEDIANA
        self.habitacion.save(update_fields=['tipo'])
        reserva=self.reserva(fecha_entrada=self.entrada+timedelta(days=5),fecha_salida=self.entrada+timedelta(days=7),descuento_aplicado=Decimal('2000'))
        datos={'habitacion':self.habitacion,'fecha_entrada':self.entrada+timedelta(days=10),'fecha_salida':self.entrada+timedelta(days=13),'numero_huespedes':3,'motivo':'Cambio solicitado por el titular'}
        modificada=modificar_por_personal(reserva.pk,self.administrador,datos)
        self.assertEqual(modificada.monto_total,Decimal('57000'))
        self.assertEqual(modificada.numero_huespedes,3)
        self.reserva(fecha_entrada=self.entrada+timedelta(days=15),fecha_salida=self.entrada+timedelta(days=17))
        with self.assertRaises(ValidationError):modificar_por_personal(reserva.pk,self.administrador,dict(datos,fecha_entrada=self.entrada+timedelta(days=15),fecha_salida=self.entrada+timedelta(days=18)))
        self.assertEqual(BitacoraReserva.objects.filter(accion='MODIFICAR_RESERVA').count(),1)

    def test_mantenimiento_no_admite_huesped_o_reserva_futura(self):
        reserva=self.reserva()
        with self.assertRaises(ValidationError):cambiar_estado_habitacion(self.habitacion.pk,self.administrador,'MANTENIMIENTO','Reparación')
        cancelar_por_personal(reserva.pk,self.administrador,'Reasignación')
        cambiar_estado_habitacion(self.habitacion.pk,self.administrador,'MANTENIMIENTO','Reparación')
        cambiar_estado_habitacion(self.habitacion.pk,self.administrador,'DISPONIBLE','Reparación terminada')
        self.assertEqual(BitacoraReserva.objects.filter(accion='ESTADO_HABITACION').count(),2)

    def test_dashboard_cuatro_estados_y_retencion_vencida_no_bloquea(self):
        self.reserva()
        ocupada=Habitacion.objects.create(numero=102,tipo='MEDIANA',precio_base_noche=Decimal('30000'))
        self.reserva(habitacion=ocupada,estado='EN_ESTADIA')
        Habitacion.objects.create(numero=103,tipo='GRANDE',precio_base_noche=Decimal('40000'),estado_actual='MANTENIMIENTO')
        libre=Habitacion.objects.create(numero=104,tipo='NORMAL',precio_base_noche=Decimal('20000'))
        self.reserva(habitacion=libre,estado='RETENIDA',retencion_hasta=timezone.now()-timedelta(seconds=1))
        self.assertEqual(datos_dashboard(timezone.localdate())['conteo'],{'DISPONIBLE':1,'RESERVADA':1,'OCUPADA':1,'MANTENIMIENTO':1})

    def test_clientes_frecuentes_prioriza_estadias_completadas(self):
        self.reserva(estado='COMPLETADA')
        self.reserva(estado='COMPLETADA')
        otro=Usuario.objects.create_user(email='otro@example.com',nombre='Luis',apellido='Sur',rut='55555555-5',telefono='912345678')
        cliente=Cliente.objects.create(usuario=otro)
        for _ in range(3):self.reserva(cliente=cliente)
        listado=list(clientes_frecuentes())
        self.assertEqual(listado[0].pk,self.cliente_hotel.pk)
        self.assertEqual(listado[0].estadias,2)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_campana_cupon_personal_envio_y_doble_peticion_no_duplica(self):
        datos=self.campana_datos()
        campana,creada=crear_campana(self.administrador,datos)
        self.assertTrue(creada)
        enviar_campana(campana,self.administrador)
        enviar_campana(campana,self.administrador)
        self.assertFalse(crear_campana(self.administrador,datos)[1])
        self.assertEqual(CuponDescuento.objects.count(),1)
        self.assertEqual(len(mail.outbox),1)
        envio=EnvioPromocion.objects.get()
        self.assertEqual(envio.estado,'ENVIADO')
        self.assertEqual(envio.cupon.cliente,self.cliente_hotel)
        self.assertIn(envio.cupon.codigo,mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_error_de_correo_se_puede_reintentar_sin_otro_cupon(self):
        campana,_=crear_campana(self.administrador,self.campana_datos())
        with patch('hotel.promociones.EmailMessage.send',side_effect=OSError('Servidor de prueba no disponible')):enviar_campana(campana,self.administrador)
        self.assertEqual(EnvioPromocion.objects.get().estado,'ERROR')
        enviar_campana(campana,self.administrador,reintentar=True)
        self.assertEqual(EnvioPromocion.objects.get().estado,'ENVIADO')
        self.assertEqual(CuponDescuento.objects.count(),1)

    def test_cupon_se_aplica_al_alojamiento_no_se_acumula_y_se_consume(self):
        reserva=preparar_pago(self.seleccion(),self.usuario)
        cupon=CuponDescuento.objects.create(codigo='PRUEBA10',cliente=self.cliente_hotel,porcentaje=10,fecha_vencimiento=timezone.localdate()+timedelta(days=30))
        aplicar_cupon_reserva(reserva.pk,self.usuario,cupon.codigo)
        reserva.refresh_from_db();self.assertEqual(reserva.descuento_aplicado,Decimal('4000'))
        confirmada,error=confirmar_pago(reserva.pk,self.datos_pago(),revision_pago(reserva))
        self.assertIsNone(error);self.assertEqual(confirmada.monto_total,Decimal('36000'))
        cupon.refresh_from_db();self.assertEqual(cupon.estado,'USADO')

    def test_cupon_ajeno_o_vencido_no_se_acepta_y_quitarlo_restituye_bienvenida(self):
        reserva=preparar_pago(self.seleccion(),self.usuario)
        vencido=CuponDescuento.objects.create(codigo='VENCIDO',cliente=self.cliente_hotel,porcentaje=10,fecha_vencimiento=timezone.localdate()-timedelta(days=1))
        with self.assertRaises(ErrorReserva):aplicar_cupon_reserva(reserva.pk,self.usuario,vencido.codigo)
        activo=CuponDescuento.objects.create(codigo='ACTIVO',cliente=self.cliente_hotel,porcentaje=10,fecha_vencimiento=timezone.localdate()+timedelta(days=5))
        aplicar_cupon_reserva(reserva.pk,self.usuario,activo.codigo)
        aplicar_cupon_reserva(reserva.pk,self.usuario,'')
        reserva.refresh_from_db();self.assertEqual(reserva.descuento_aplicado,Decimal('2000'))
        with self.assertRaises(ErrorReserva):aplicar_cupon_reserva(reserva.pk,self.admin,activo.codigo)

    def test_bitacora_no_se_edita_ni_borra(self):
        reserva=self.reserva();cancelar_por_personal(reserva.pk,self.administrador,'Solicitud del titular')
        evento=BitacoraReserva.objects.get()
        evento.motivo='Alterado'
        with self.assertRaises(ValidationError):evento.save()
        with self.assertRaises(ValidationError):evento.delete()

    def test_acciones_por_http_y_rut_formateado(self):
        reserva=self.reserva()
        self.client.force_login(self.recepcionista_user)
        respuesta=self.client.get(reverse('hotel:panel_reservas'),{'q':'33.333.333-3'})
        self.assertContains(respuesta,reserva.codigo)
        self.assertEqual(self.client.get(reverse('hotel:panel_reserva',args=[reserva.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('hotel:panel_accion',args=[reserva.pk]),{'accion':'checkin'}).status_code,302)
        reserva.refresh_from_db();self.assertEqual(reserva.estado,'EN_ESTADIA')
        self.assertEqual(self.client.post(reverse('hotel:panel_habitacion',args=[self.habitacion.pk]),{'estado':'DISPONIBLE','motivo':'Forzado'}).status_code,403)

    def test_extras_en_estadia_segun_habitacion_y_disponibilidad(self):
        reserva = self.reserva()
        registrar_checkin(reserva.pk, self.recepcionista)
        outdoor = ServicioAdicional.objects.create(nombre='Senderismo', precio=Decimal('8000'), categoria='OUTDOOR')
        agua = ServicioAdicional.objects.create(nombre='Agua mineral', precio=Decimal('2000'), categoria='PRODUCTO')
        spa = ServicioAdicional.objects.create(nombre='Spa privado', precio=Decimal('15000'), categoria='SERVICIO', tipo_minimo='GRANDE')

        self.client.force_login(self.usuario)
        historial = reverse('hotel:mis_reservas')
        pedir = reverse('hotel:solicitar_extra', args=[reserva.codigo])
        datos = {'servicio': outdoor.pk, 'cantidad': 1, 'token': uuid.uuid4()}
        respuesta = self.client.get(historial)
        self.assertContains(respuesta, 'Senderismo')
        self.assertContains(respuesta, 'Agua mineral')
        self.assertNotContains(respuesta, 'Spa privado')
        self.client.post(pedir, dict(datos, servicio=spa.pk, token=uuid.uuid4()))
        self.assertFalse(reserva.solicitudes_extra.exists())
        self.client.post(pedir, datos)
        self.client.post(pedir, datos)
        self.assertEqual(reserva.solicitudes_extra.count(), 1)
        self.assertEqual(reserva.solicitudes_extra.get().estado, SolicitudExtra.Estado.CONFIRMADA)
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('48000'))

        self.client.post(pedir, dict(datos, servicio=agua.pk, token=uuid.uuid4()))
        pendiente = reserva.solicitudes_extra.get(servicio=agua)
        self.assertEqual(pendiente.estado, SolicitudExtra.Estado.PENDIENTE)
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('48000'))
        estado = reverse('hotel:panel_estado_extra', args=[agua.pk])
        self.assertEqual(self.client.post(estado, {'activo': '0'}).status_code, 403)

        self.client.force_login(self.recepcionista_user)
        self.assertContains(self.client.get(reverse('hotel:panel_extras')), 'Disponibilidad de extras')
        self.client.post(estado, {'activo': '0'})
        agua.refresh_from_db()
        self.assertFalse(agua.activo)
        accion = reverse('hotel:panel_accion', args=[reserva.pk])
        solicitud = {'accion': 'resolver_extra', 'solicitud_extra': pendiente.pk, 'decision': 'confirmar', 'respuesta': 'Entrega en la habitación'}
        self.client.post(accion, solicitud)
        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, SolicitudExtra.Estado.PENDIENTE)

        self.client.force_login(self.admin)
        self.client.post(estado, {'activo': '1'})
        self.client.post(accion, solicitud)
        self.client.post(accion, solicitud)
        pendiente.refresh_from_db()
        reserva.refresh_from_db()
        self.assertEqual(pendiente.estado, SolicitudExtra.Estado.CONFIRMADA)
        self.assertEqual(reserva.monto_total, Decimal('50000'))
        self.assertEqual(reserva.eventos.filter(accion='RESOLVER_EXTRA').count(), 1)

        self.habitacion.tipo = Habitacion.Tipo.GRANDE
        self.habitacion.save(update_fields=['tipo'])
        self.client.force_login(self.usuario)
        self.assertContains(self.client.get(historial), 'Spa privado')
        self.assertContains(self.client.get(historial), 'Entrega en la habitación')

    def test_recepcion_agrega_extra_durante_estadia_y_registra_un_solo_cargo(self):
        reserva = self.reserva(descuento_aplicado=Decimal('2000'))
        desayuno = ServicioAdicional.objects.create(nombre='Desayuno', precio=Decimal('5000'))
        otro = ServicioAdicional.objects.create(nombre='Spa', precio=Decimal('7500'))
        self.client.force_login(self.recepcionista_user)
        accion = reverse('hotel:panel_accion', args=[reserva.pk])
        detalle = reverse('hotel:panel_reserva', args=[reserva.pk])
        datos = {'accion': 'agregar_extra', 'servicio': desayuno.pk, 'cantidad': 2, 'solicitud': uuid.uuid4()}
        self.assertNotContains(self.client.get(detalle), 'Agregar extra')
        self.client.post(accion, datos)
        self.assertFalse(reserva.detalles.exists())

        registrar_checkin(reserva.pk, self.recepcionista)
        self.assertContains(self.client.get(detalle), 'Agregar extra')
        self.client.post(accion, datos)
        self.client.post(accion, datos)
        desayuno.precio = Decimal('6000')
        desayuno.save(update_fields=['precio'])
        self.client.post(accion, dict(datos, cantidad=1, solicitud=uuid.uuid4()))
        self.client.post(accion, dict(datos, servicio=otro.pk, cantidad=1, solicitud=uuid.uuid4()))
        reserva.refresh_from_db()
        self.assertEqual(reserva.detalles.get(servicio=desayuno).cantidad, 3)
        self.assertEqual(reserva.detalles.get(servicio=desayuno).precio_unitario, Decimal('5000'))
        self.assertEqual(reserva.monto_total, Decimal('60500'))
        self.assertEqual(reserva.eventos.filter(accion='AGREGAR_EXTRA').count(), 3)
        self.assertContains(self.client.get(detalle), 'Desayuno × 3')

        with self.assertRaises(ValidationError):
            registrar_checkout(reserva.pk, self.recepcionista)
        self.assertEqual(reserva.monto_pagado_inicial, Decimal('38000'))
        with self.assertRaises(ValidationError):
            pagar_saldo_estadia(reserva.pk, self.recepcionista, Decimal('1'))
        revision = self.client.get(detalle).context['revision_saldo']
        formulario = {'accion': 'pagar_saldo', 'importe': '22500.00', 'confirmar': 'on', 'revision': revision}
        self.assertEqual(self.client.post(accion, formulario).status_code, 302)
        self.assertEqual(self.client.post(accion, formulario).status_code, 302)
        self.assertEqual(reserva.eventos.filter(accion='PAGO_ADICIONAL').count(), 1)
        reserva.refresh_from_db()
        self.assertEqual(reserva.saldo_pendiente, 0)
        registrar_checkout(reserva.pk, self.recepcionista)
        self.client.post(accion, dict(datos, solicitud=uuid.uuid4()))
        self.assertEqual(reserva.detalles.get(servicio=desayuno).cantidad, 3)

    def test_seleccion_masiva_esta_en_formulario_de_promocion(self):
        self.client.force_login(self.admin)
        listado = self.client.get(reverse('hotel:panel_clientes'))
        self.assertNotContains(listado, 'Enviar a todos los clientes')
        respuesta = self.client.get(reverse('hotel:panel_promociones'))
        self.assertContains(respuesta, 'data-recipient-action="select"')
        self.assertContains(respuesta, 'data-recipient-action="clear"')
        self.assertContains(respuesta, 'Marcar todos')
        self.assertContains(respuesta, 'Desmarcar todos')
        self.assertContains(respuesta, 'panel.js?v=destinatarios-3')
        self.assertContains(respuesta, 'data-recipient-search')
        self.assertContains(respuesta, 'Ana Prueba · RUT 33333333-3')
        self.assertNotContains(respuesta, ' checked')
        self.assertFalse(CampanaPromocion.objects.exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_campana_formulario_y_detalle_se_renderizan(self):
        self.client.force_login(self.admin)
        datos=self.campana_datos();datos['clientes']=[self.cliente_hotel.pk]
        respuesta=self.client.post(reverse('hotel:panel_promociones'),datos)
        self.assertEqual(respuesta.status_code,302)
        self.assertEqual(self.client.get(respuesta.url).status_code,200)
        envio=EnvioPromocion.objects.get()
        self.assertContains(self.client.get(reverse('hotel:panel_correo',args=[envio.pk])),envio.cupon.codigo)
