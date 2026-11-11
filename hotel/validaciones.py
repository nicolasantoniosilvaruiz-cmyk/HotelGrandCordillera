import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator


validar_arroba = RegexValidator('@', 'Ingresa un correo que contenga @.')


def normalizar_rut(valor):
    return (valor or '').strip().replace('.', '').replace(' ', '').upper()


def validar_rut(valor):
    rut = normalizar_rut(valor)
    if not re.fullmatch(r'[0-9]{1,8}-?[0-9K]', rut):
        raise ValidationError('Ingresa un RUT chileno válido, con su dígito verificador.')
    cuerpo, digito = rut[:-1].rstrip('-'), rut[-1]
    numero = int(cuerpo)
    suma = sum(int(cifra) * (2 + posicion % 6) for posicion, cifra in enumerate(reversed(cuerpo)))
    esperado = '0123456789K'[(11 - suma % 11) % 11]
    if not numero or digito != esperado:
        raise ValidationError('Ingresa un RUT chileno válido, con su dígito verificador.')
    return f'{numero}-{digito}'
