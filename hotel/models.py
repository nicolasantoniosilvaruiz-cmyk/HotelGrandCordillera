import secrets
import string
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F
from django.db.models.functions import Lower
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