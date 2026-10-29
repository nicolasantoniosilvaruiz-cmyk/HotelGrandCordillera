import secrets
import string
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.models import Q, F
from django.db.models.functions import Lower
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from .managers import UsuarioManager


# =========================================================
# FUNCIONES AUXILIARES
# =========================================================

def generar_codigo_reserva():
    caracteres = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(caracteres) for _ in range(8))


def calcular_vencimiento_retencion():
    return timezone.now() + timedelta(minutes=5)


# =========================================================
# USUARIOS Y PERFILES
# =========================================================

class Usuario(AbstractUser):
    username = None
    first_name = None
    last_name = None

    rut = models.CharField(
        max_length=12,
        unique=True,
        verbose_name="RUT",
    )

    nombre = models.CharField(max_length=100)

    apellido = models.CharField(max_length=100)

    email = models.EmailField(
        unique=True,
        verbose_name="Correo electrónico",
    )

    telefono = models.CharField(
        max_length=25,
        verbose_name="Teléfono",
    )

    USERNAME_FIELD = "email"

    REQUIRED_FIELDS = [
        "rut",
        "nombre",
        "apellido",
        "telefono",
    ]

    objects = UsuarioManager()

    class Meta:
        db_table = "usuario"
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="usuario_email_unico_sin_mayusculas",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()

        # Normalización de presentación; no valida el dígito verificador.
        self.rut = (
            self.rut.strip()
            .replace(".", "")
            .replace(" ", "")
            .upper()
        )

        super().save(*args, **kwargs)

    def get_full_name(self):
        return f"{self.nombre} {self.apellido}".strip()

    def get_short_name(self):
        return self.nombre

    def __str__(self):
        return self.get_full_name()


class Cliente(models.Model):
    usuario = models.OneToOneField(
        Usuario,
        on_delete=models.PROTECT,
        related_name="cliente",
    )

    class Meta:
        db_table = "cliente"
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self):
        return str(self.usuario)


class PersonalHotel(models.Model):
    class Rol(models.TextChoices):
        RECEPCIONISTA = "RECEPCIONISTA", "Recepcionista"
        ADMINISTRADOR = "ADMINISTRADOR", "Administrador"

    usuario = models.OneToOneField(
        Usuario,
        on_delete=models.PROTECT,
        related_name="personal_hotel",
    )

    codigo_empleado = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Código de empleado",
    )

    rol = models.CharField(
        max_length=20,
        choices=Rol.choices,
    )

    class Meta:
        db_table = "personal_hotel"
        verbose_name = "Personal del hotel"
        verbose_name_plural = "Personal del hotel"

    def __str__(self):
        return f"{self.usuario} - {self.get_rol_display()}"


# =========================================================
# HABITACIONES Y SERVICIOS
# =========================================================

class Habitacion(models.Model):
    class Tipo(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        MEDIANA = "MEDIANA", "Mediana"
        GRANDE = "GRANDE", "Grande / Suite"

    class Estado(models.TextChoices):
        DISPONIBLE = "DISPONIBLE", "Disponible"
        RESERVADA = "RESERVADA", "Reservada"
        OCUPADA = "OCUPADA", "Ocupada"
        MANTENIMIENTO = "MANTENIMIENTO", "En mantenimiento"

    numero = models.PositiveIntegerField(
        unique=True,
        validators=[MinValueValidator(1)],
    )

    tipo = models.CharField(
        max_length=10,
        choices=Tipo.choices,
    )

    precio_base_noche = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    estado_actual = models.CharField(
        max_length=15,
        choices=Estado.choices,
        default=Estado.DISPONIBLE,
    )

    class Meta:
        db_table = "habitacion"
        verbose_name = "Habitación"
        verbose_name_plural = "Habitaciones"
        ordering = ["numero"]

        constraints = [
            models.CheckConstraint(
                condition=Q(precio_base_noche__gte=0),
                name="habitacion_precio_no_negativo",
            ),
        ]

    def __str__(self):
        return f"Habitación {self.numero} - {self.get_tipo_display()}"


class ServicioAdicional(models.Model):
    nombre = models.CharField(
        max_length=100,
        unique=True,
    )

    precio = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "servicio_adicional"
        verbose_name = "Servicio adicional"
        verbose_name_plural = "Servicios adicionales"

        constraints = [
            models.CheckConstraint(
                condition=Q(precio__gte=0),
                name="servicio_precio_no_negativo",
            ),
        ]

    def __str__(self):
        return self.nombre


# =========================================================
# RESERVAS
# =========================================================

class Reserva(models.Model):
    class Estado(models.TextChoices):
        RETENIDA = "RETENIDA", "Retención temporal"
        CONFIRMADA = "CONFIRMADA", "Confirmada"
        EN_ESTADIA = "EN_ESTADIA", "En estadía"
        COMPLETADA = "COMPLETADA", "Completada"
        CANCELADA = "CANCELADA", "Cancelada"

    codigo = models.CharField(
        max_length=8,
        unique=True,
        default=generar_codigo_reserva,
        editable=False,
    )

    # Puede quedar vacío para reservas de invitados.
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="reservas",
        null=True,
        blank=True,
    )

    personal = models.ForeignKey(
        PersonalHotel,
        on_delete=models.PROTECT,
        related_name="reservas_gestionadas",
        null=True,
        blank=True,
    )

    habitacion = models.ForeignKey(
        Habitacion,
        on_delete=models.PROTECT,
        related_name="reservas",
    )

    fecha_entrada = models.DateTimeField()

    fecha_salida = models.DateTimeField()

    numero_huespedes = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(6)],
        verbose_name="Número de huéspedes",
    )

    pago_simulado = models.BooleanField(
        default=False, editable=False, verbose_name="Pago de prueba aprobado",
    )

    fecha_creacion = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    fecha_confirmacion = models.DateTimeField(
        null=True,
        blank=True,
    )

    fecha_checkin = models.DateTimeField(
        null=True,
        blank=True,
    )

    estado = models.CharField(
        max_length=12,
        choices=Estado.choices,
        default=Estado.RETENIDA,
    )

    retencion_hasta = models.DateTimeField(
        default=calcular_vencimiento_retencion,
        null=True,
        blank=True,
    )

    # Datos del titular, tenga o no una cuenta.
    # Pueden estar vacíos durante la retención inicial.
    titular_rut = models.CharField(
        max_length=12,
        blank=True,
    )

    titular_nombre = models.CharField(
        max_length=100,
        blank=True,
    )

    titular_apellido = models.CharField(
        max_length=100,
        blank=True,
    )

    titular_correo = models.EmailField(blank=True)

    titular_telefono = models.CharField(
        max_length=25,
        blank=True,
    )

    # Valores históricos de la reserva.
    precio_noche_pactado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    descuento_aplicado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    monto_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        db_table = "reserva"
        verbose_name = "Reserva"
        verbose_name_plural = "Reservas"
        ordering = ["-fecha_creacion"]

        constraints = [
            models.CheckConstraint(
                condition=Q(fecha_salida__gt=F("fecha_entrada")),
                name="reserva_fechas_ordenadas",
            ),
            models.CheckConstraint(
                condition=Q(
                    precio_noche_pactado__gte=0,
                    descuento_aplicado__gte=0,
                    monto_total__gte=0,
                ),
                name="reserva_importes_no_negativos",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "habitacion",
                    "estado",
                    "fecha_entrada",
                    "fecha_salida",
                ],
                name="reserva_disponibilidad_idx",
            ),
        ]

    @staticmethod
    def contar_noches(entrada, salida):
        if not entrada or not salida or salida <= entrada:
            raise ValidationError({
                "fecha_salida": "La salida debe ser posterior a la entrada.",
            })
        zona = timezone.get_default_timezone()
        entrada_local = timezone.localtime(entrada, zona) if timezone.is_aware(entrada) else entrada
        salida_local = timezone.localtime(salida, zona) if timezone.is_aware(salida) else salida
        return max(1, (salida_local.date() - entrada_local.date()).days)

    def clean(self):
        super().clean()
        if self.fecha_entrada and self.fecha_salida:
            self.contar_noches(self.fecha_entrada, self.fecha_salida)

    def calcular_total(self, *, using=None, valores=None):
        valores = valores or {
            campo: getattr(self, campo)
            for campo in (
                "fecha_entrada", "fecha_salida",
                "precio_noche_pactado", "descuento_aplicado",
            )
        }
        noches = self.contar_noches(valores["fecha_entrada"], valores["fecha_salida"])
        servicios = Decimal("0")
        if not self._state.adding:
            detalles = DetalleReserva.objects.using(using or self._state.db or "default")
            servicios = sum(
                (cantidad * precio for cantidad, precio in detalles.filter(
                    reserva_id=self.pk
                ).values_list("cantidad", "precio_unitario")),
                Decimal("0"),
            )
        subtotal = noches * valores["precio_noche_pactado"] + servicios
        return max(Decimal("0"), subtotal - valores["descuento_aplicado"]).quantize(Decimal("0.01"))

    def recalcular_total(self, *, using=None):
        # Leer los valores guardados evita usar un objeto de reserva desactualizado.
        using = using or self._state.db or "default"
        actual = type(self).objects.using(using).get(pk=self.pk)
        actual.save(using=using, update_fields=["monto_total"])
        self.monto_total = actual.monto_total
        return self.monto_total

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                return

        using = kwargs.get("using") or self._state.db or "default"
        anterior = None
        if not self._state.adding:
            anterior = (
                type(self).objects.using(using)
                .filter(pk=self.pk)
                .values(
                    "cliente_id", "habitacion_id", "fecha_entrada", "fecha_salida",
                    "precio_noche_pactado", "descuento_aplicado",
                )
                .first()
            )

        campos_actualizados = set()
        guardar_cliente = update_fields is None or bool(
            {"cliente", "cliente_id"} & update_fields
        )
        guardar_habitacion = update_fields is None or bool(
            {"habitacion", "habitacion_id"} & update_fields
        )

        # Conservar los datos pactados, salvo que cambie la relación.
        if guardar_cliente and self.cliente_id and (
            anterior is None or anterior["cliente_id"] != self.cliente_id
        ):
            usuario = self.cliente.usuario
            for campo, origen in (
                ("titular_rut", "rut"),
                ("titular_nombre", "nombre"),
                ("titular_apellido", "apellido"),
                ("titular_correo", "email"),
                ("titular_telefono", "telefono"),
            ):
                setattr(self, campo, getattr(usuario, origen))
                campos_actualizados.add(campo)

        if guardar_habitacion and self.habitacion_id and (
            anterior is None
            or anterior["habitacion_id"] != self.habitacion_id
        ):
            self.precio_noche_pactado = self.habitacion.precio_base_noche
            campos_actualizados.add("precio_noche_pactado")

        # Un guardado parcial debe calcular con los valores que quedarán en la BD.
        campos_calculo = (
            "fecha_entrada", "fecha_salida",
            "precio_noche_pactado", "descuento_aplicado",
        )
        valores = {}
        for campo in campos_calculo:
            if anterior is not None and update_fields is not None and campo not in (
                update_fields | campos_actualizados
            ):
                valores[campo] = anterior[campo]
            else:
                valores[campo] = getattr(self, campo)
        self.monto_total = self.calcular_total(using=using, valores=valores)
        campos_actualizados.add("monto_total")

        if update_fields is not None:
            kwargs["update_fields"] = update_fields | campos_actualizados

        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.codigo} - Habitación {self.habitacion.numero}"


class DetalleReserva(models.Model):
    reserva = models.ForeignKey(
        Reserva,
        on_delete=models.CASCADE,
        related_name="detalles",
    )

    servicio = models.ForeignKey(
        ServicioAdicional,
        on_delete=models.PROTECT,
        related_name="detalles",
    )

    cantidad = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    # Conserva el precio contratado aunque cambie el catálogo.
    precio_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    observaciones = models.TextField(blank=True)

    class Meta:
        db_table = "detalle_reserva"
        verbose_name = "Detalle de reserva"
        verbose_name_plural = "Detalles de reserva"

        constraints = [
            models.UniqueConstraint(
                fields=["reserva", "servicio"],
                name="detalle_servicio_unico",
            ),
            models.CheckConstraint(
                condition=Q(
                    cantidad__gt=0,
                    precio_unitario__gte=0,
                ),
                name="detalle_valores_validos",
            ),
        ]

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def save(self, *args, **kwargs):
        if kwargs.get("update_fields") is not None and not kwargs["update_fields"]:
            return
        using = kwargs.get("using") or self._state.db or "default"
        with transaction.atomic(using=using):
            anterior_id = None
            if not self._state.adding:
                anterior_id = type(self).objects.using(using).filter(
                    pk=self.pk
                ).values_list("reserva_id", flat=True).first()
            resultado = super().save(*args, **kwargs)
            actual_id = type(self).objects.using(using).values_list(
                "reserva_id", flat=True
            ).get(pk=self.pk)
            for reserva in Reserva.objects.using(using).filter(
                pk__in={anterior_id, actual_id} - {None}
            ):
                reserva.recalcular_total(using=using)
            return resultado

    def __str__(self):
        return f"{self.reserva.codigo} - {self.servicio.nombre}"


# =========================================================
# CUPONES
# =========================================================

class CuponDescuento(models.Model):
    class Estado(models.TextChoices):
        ACTIVO = "ACTIVO", "Activo"
        USADO = "USADO", "Usado"
        ANULADO = "ANULADO", "Anulado"

    codigo = models.CharField(
        max_length=30,
        unique=True,
    )

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="cupones",
    )

    # Conserva la cardinalidad del diagrama:
    # una reserva puede asociarse con varios cupones.
    reserva = models.ForeignKey(
        Reserva,
        on_delete=models.PROTECT,
        related_name="cupones",
        null=True,
        blank=True,
    )

    porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal("100")),
        ],
    )

    fecha_vencimiento = models.DateField()

    estado = models.CharField(
        max_length=10,
        choices=Estado.choices,
        default=Estado.ACTIVO,
    )

    class Meta:
        db_table = "cupon_descuento"
        verbose_name = "Cupón de descuento"
        verbose_name_plural = "Cupones de descuento"

        constraints = [
            models.CheckConstraint(
                condition=Q(
                    porcentaje__gt=0,
                    porcentaje__lte=100,
                ),
                name="cupon_porcentaje_valido",
            ),
        ]

    def __str__(self):
        return self.codigo


# =========================================================
# BITÁCORA DE ACCIONES ADMINISTRATIVAS
# =========================================================

class BitacoraReserva(models.Model):
    reserva = models.ForeignKey(
        Reserva,
        on_delete=models.PROTECT,
        related_name="eventos",
    )

    personal = models.ForeignKey(
        PersonalHotel,
        on_delete=models.PROTECT,
        related_name="eventos",
    )

    fecha = models.DateTimeField(default=timezone.now)

    accion = models.CharField(max_length=50)

    motivo = models.TextField()

    class Meta:
        db_table = "bitacora_reserva"
        verbose_name = "Registro de bitácora"
        verbose_name_plural = "Bitácora de reservas"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.reserva.codigo} - {self.accion}"


@receiver(post_delete, sender=DetalleReserva)
def recalcular_total_al_eliminar_servicio(sender, instance, using, **kwargs):
    # También cubre eliminaciones múltiples desde el administrador.
    reserva = Reserva.objects.using(using).filter(pk=instance.reserva_id).first()
    if reserva is not None:
        reserva.recalcular_total(using=using)
