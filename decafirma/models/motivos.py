"""Los errores de DecaFirma, en castellano.

La API contesta en inglés: {"error": {"code", "message", "details"}}. Cuando una
regla de negocio impide emitir, corregir, anular o hacer una ruta (422,
«unprocessable»), «details» trae un motivo de una lista cerrada («reason») con
sus datos; aquí se dice en castellano con los nombres de campo de siempre. Un
motivo que no está aquí (uno nuevo de la API) se enseña con el «message» en
inglés tal cual. Lista: https://decafirma.com/desarrolladores#motivos
"""

# Los campos del envío en la API, con su nombre en castellano.
CAMPOS = {
    'reference': 'Referencia',
    'shipperName': 'Cargador contractual',
    'shipperTaxId': 'NIF del cargador',
    'shipperAddress': 'Domicilio del cargador',
    'carrierName': 'Transportista efectivo',
    'carrierTaxId': 'NIF del transportista',
    'carrierPhone': 'Teléfono del transportista',
    'carrierEmail': 'Correo del transportista',
    'consigneeName': 'Destinatario',
    'consigneeTaxId': 'NIF del destinatario',
    'consigneeContact': 'Persona de contacto del destinatario',
    'consigneeContactPhone': 'Teléfono de contacto del destinatario',
    'consigneeEmail': 'Correo del destinatario',
    'originName': 'Nombre del lugar de origen',
    'originAddress': 'Dirección de origen',
    'originPostcode': 'Código postal de origen',
    'originCity': 'Lugar de origen',
    'originProvince': 'Provincia de origen',
    'originPhone': 'Teléfono de origen',
    'originContact': 'Contacto en origen',
    'destinationName': 'Nombre del lugar de destino',
    'destinationAddress': 'Dirección de destino',
    'destinationPostcode': 'Código postal de destino',
    'destinationCity': 'Lugar de destino',
    'destinationProvince': 'Provincia de destino',
    'destinationPhone': 'Teléfono de destino',
    'goodsDescription': 'Naturaleza de la mercancía',
    'weightKg': 'Peso',
    'packages': 'Bultos',
    'transportDate': 'Fecha del transporte',
    'transportTime': 'Hora del transporte',
    'plate': 'Matrícula del vehículo',
    'trailerPlate': 'Matrícula del remolque',
    'vehicleTonnage': 'Tonelaje del vehículo',
    'driverName': 'Conductor',
    'driverTaxId': 'NIF del conductor',
    'driverPhone': 'Teléfono del conductor',
    'noteKind': 'Tipo de albarán',
    'freightTerms': 'Portes (pagados / debidos)',
    'amount': 'Importe',
    'terms': 'Condiciones',
    'notes': 'Observaciones',
}

DOCUMENTO = {'deca': 'el DeCA', 'delivery_note': 'el albarán'}
EN_RUTA = {'shipperTaxId': 'el cargador no es el mismo', 'carrierTaxId': 'el transportista no es el mismo',
           'transportDate': 'la fecha no es la misma', 'plate': 'la matrícula no es la misma'}


def campo(nombre):
    return CAMPOS.get(nombre, nombre)


def _campos(d):
    return ', '.join(campo(f) for f in d.get('fields') or [])


def _donde(d):
    """«Parada 2: » / «Envío 3: » delante, si el problema es de una parada de la ruta."""
    if d.get('stop'):
        return f"Parada {d['stop']}: "
    if d.get('position'):
        return f"Envío {d['position']}: "
    return ''


def _falta(d):
    donde = _donde(d)
    return f'{donde}falta {_campos(d)}.' if donde else f'Faltan datos para emitir: {_campos(d)}.'


def _nif(d):
    malo = 'está incompleto: le faltan cifras' if d.get('problem') == 'incomplete' else 'no es válido: revisa las cifras y la letra'
    return f"{_donde(d)}El {campo(d.get('field'))} ({d.get('value')}) {malo}."


def _pocas_paradas(d):
    if (d.get('minimum') or 2) >= 2:
        return 'Una ruta necesita al menos dos paradas. Para una sola, emite un DeCA normal.'
    return 'Añade al menos una parada.'


def _ya_existe(d):
    uno = 'un albarán' if d.get('document') == 'delivery_note' else 'un DeCA'
    return f"Este envío ya tiene {uno} vigente ({d.get('reference')}). Modifícalo o anúlalo."


def _gratis(d):
    cuales = 'albaranes' if d.get('document') == 'delivery_note' else 'DeCA'
    return (f"Has hecho los {d.get('limit')} {cuales} gratis de este mes. "
            'Paga solo este mes, o elige plan, en Configuración → Cuenta y plan.')


def _flota(d):
    return (f"Tu plan cubre {d.get('maxVehicles')} vehículos y llevas más de {d.get('graceDays')} días con uno más. "
            f"Para seguir emitiendo con {d.get('vehicle') or d.get('plate') or 'ese vehículo'}, "
            'pide el cambio de plan en Configuración → Cuenta y plan.')


MOTIVOS = {
    'missing_fields': _falta,
    'invalid_tax_id': _nif,
    'transport_date_expired': lambda d: (
        f"La fecha del transporte es el {d.get('value')}: hace más de {d.get('days')} días, "
        'así que el documento nacería con la descarga ya cerrada. Revisa la fecha.'),
    'missing_delivery_note_number': lambda d: (
        f"{_donde(d)}Falta el número del albarán: escríbelo o elige una serie para que lo ponga DecaFirma."),
    'invalid_delivery_note_number': lambda d: (
        f"El número de albarán {d.get('number')} solo puede llevar letras, cifras, guiones, barras y puntos (hasta 40)."),
    'duplicate_delivery_note_number': lambda d: f"Ya tienes un documento con el número {d.get('number')}. Pon otro.",
    'document_already_exists': _ya_existe,
    'document_voided': lambda d: f"{d.get('reference')} está anulado: un documento anulado no se modifica.",
    'document_already_voided': lambda d: f"{d.get('reference')} ya estaba anulado.",
    'delivery_note_signed': lambda d: (
        f"El albarán {d.get('reference')} ya está firmado: no se modifica. Anúlalo y emite uno nuevo."),
    'reason_required': lambda d: 'Falta el motivo.',
    'shipment_in_route': lambda d: 'Este envío va en una ruta: su DeCA es el de la ruta, no uno suyo.',
    'shipment_not_in_route': lambda d: 'Este envío no va en esa ruta.',
    'shipments_have_own_deca': lambda d: (
        f"Hay envíos que ya tienen su propio DeCA ({', '.join(d.get('references') or [])}). "
        'Anúlalos antes de sacar el de la ruta.'),
    'route_too_few_stops': _pocas_paradas,
    'route_field_mismatch': lambda d: (
        f"{_donde(d)}{EN_RUTA.get(d.get('field'), 'un dato no es el mismo')} que en el resto de la ruta. "
        'Un DeCA de ruta exige el mismo cargador, transportista, fecha y vehículo en todos los envíos.'),
    'route_goods_only': lambda d: f'{_donde(d)}las rutas son de mercancías.',
    'route_without_deca': lambda d: 'Esta ruta no tiene un DeCA vivo.',
    'deca_not_convertible': lambda d: 'Ese DeCA ya no se puede convertir en ruta (está anulado o ya es una ruta).',
    'deca_already_in_route': lambda d: 'El envío de ese DeCA ya va en una ruta.',
    'shipment_not_found': lambda d: 'Envío no encontrado.',
    'document_not_found': lambda d: 'Documento no encontrado.',
    'route_not_found': lambda d: 'Ruta no encontrada.',
    'no_subscription': lambda d: 'La empresa no tiene suscripción.',
    'demo_ended': lambda d: 'La prueba ha terminado. Activa la cuenta para seguir emitiendo.',
    'account_inactive': lambda d: 'La cuenta no está activa. No se pueden emitir documentos nuevos.',
    'free_plan_not_allowed': lambda d: 'La ruta de reparto no va en el plan gratis. Actívala en Configuración → Cuenta y plan.',
    'free_plan_limit_reached': _gratis,
    'fleet_limit_exceeded': _flota,
    'field_not_allowed': lambda d: (
        f"{_campos(d)} sale en {DOCUMENTO.get(d.get('document'), 'el documento')}, y ahí solo puedes corregir bultos y peso."),
    'not_yours': lambda d: 'Tiene que ir a tu nombre: como transportista o como conductor.',
    'permission_denied': lambda d: 'No tienes permiso para sacar albaranes: emite la ruta sin ellos.',
    'email_recipients_limit': lambda d: f"Hoy ya se han mandado correos a {d.get('limit')} personas distintas desde esta cuenta. Mañana más.",
    'email_same_recipient_limit': lambda d: f"A {d.get('email')} ya le hemos mandado {d.get('limit')} correos hoy. Mañana más.",
    # La regla sin motivo propio: el texto de la app, que ya viene en castellano.
    'business_rule': lambda d: d.get('text'),
}

# Los demás códigos de error de la API (sin «reason»), en castellano.
CODIGOS = {
    'missing_api_key': 'Falta la clave de la API: Ajustes > DecaFirma.',
    'invalid_api_key': 'La clave de la API no existe o se ha revocado: pon una nueva en Ajustes > DecaFirma.',
    'rate_limited': 'Demasiadas peticiones seguidas. Prueba otra vez en un momento.',
    'plan_not_allowed': 'Tu plan de DecaFirma no incluye la conexión con tu programa.',
    'addon_required': 'Falta el módulo «Conexión con tu programa» en DecaFirma (Módulos).',
    'key_owner_forbidden': 'El dueño de la clave de la API no tiene permiso para esto.',
    'not_found': 'No está en DecaFirma (o no es de tu empresa).',
    'invalid_series': 'Esa serie no existe en DecaFirma.',
    'payload_too_large': 'Demasiado grande para mandarlo a DecaFirma.',
    'quota_exceeded': 'Has llegado al tope de lecturas de DecaFirma.',
    'service_unavailable': 'DecaFirma no está disponible ahora mismo. Prueba en un momento.',
    'upstream_error': 'DecaFirma no ha podido leerlo. Prueba otra vez en un momento.',
    'not_a_shipment': 'Ese texto no encarga ningún porte: le falta el sitio de carga, el destino o la mercancía.',
    'extraction_failed': 'DecaFirma no ha podido sacar los datos del texto.',
    'invalid_webhook_url': 'La dirección de los avisos no es válida.',
}


def _uno(d, mensaje_ingles):
    hacer = MOTIVOS.get(d.get('reason'))
    try:
        return (hacer(d) if hacer else None) or mensaje_ingles
    except Exception:  # unos datos raros no deben tapar el error
        return mensaje_ingles


def traducir(error):
    """El texto en castellano de un {code, message, details} de la API, o None si no hay error."""
    if not isinstance(error, dict):
        return error or None
    code, mensaje, d = error.get('code'), error.get('message'), error.get('details')
    if isinstance(d, dict) and d.get('reason'):
        problemas = d.get('problems')
        if isinstance(problemas, list) and len(problemas) > 1:
            texto = ' '.join(_uno(p, p.get('message')) or '' for p in problemas if isinstance(p, dict)).strip()
        else:
            texto = _uno(d, mensaje)
        if d.get('document') in DOCUMENTO and code == 'unprocessable':
            texto = f"No se ha emitido {DOCUMENTO[d['document']]}: {texto}"
        return texto or mensaje or code
    if code == 'validation_error':
        # Los fallos del cuerpo: van en details, en inglés, y nombran el campo de la API.
        detalles = ' '.join(map(str, d)) if isinstance(d, list) else ''
        return f'Datos no válidos para DecaFirma. {detalles}'.strip()
    return CODIGOS.get(code) or mensaje or code or None
