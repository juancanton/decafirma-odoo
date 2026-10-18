import json

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.client import DecafirmaClient, DecafirmaError
from ..models.decafirma_mixin import GRUPO_RESPONSABLE, comprobar_permiso
from ..models.decafirma_sync_link import TIPOS
from ..models.motivos import omitido

# Lo que acepta DecaFirma en una llamada de POST /sync: más, por tandas.
MAX_POR_LLAMADA = 500


def nif(valor):
    """El NIF como va en DecaFirma: sin espacios ni guiones y sin el «ES» de VIES que pone Odoo."""
    if not valor:
        return None
    v = valor.replace(' ', '').replace('-', '').replace('.', '').upper()
    if v.startswith('ES') and len(v) == 11:
        v = v[2:]
    return v or None


# Los campos que son sí/no: un False va (los demás, vacíos en Odoo, no).
SI_NO = {'archived', 'invite', 'drives', 'default', 'issuesDeca', 'issuesDeliveryNote', 'editsDeca', 'editsDeliveryNote',
         'billedHourly', 'manualDeliveryNoteNumber', 'askForAttachments', 'hourlyServices'}


def limpio(item):
    """Sin vacíos (None, '', el False de un campo vacío de Odoo): DecaFirma deja lo que no viene como está."""
    return {k: (v[:300] if isinstance(v, str) else v) for k, v in item.items()
            if (k in SI_NO and isinstance(v, bool)) or v not in (None, '', False, [])}


def ext_id(rec):
    """La referencia de un registro de Odoo en DecaFirma: «odoo:res.partner:42»."""
    return f'odoo:{rec._name}:{rec.id}'


class DecafirmaSyncWizard(models.TransientModel):
    """Puesta a punto: deja DecaFirma con lo que hay en Odoo (clientes, lugares, gente, vehículos…).

    Tres pasos: qué se manda, ver antes de mandar (DecaFirma contesta lo que
    haría sin guardar nada, «dryRun») y mandar. Se puede repetir: lo que ya está
    igual sale «sin cambios». Nunca borra nada en DecaFirma: lo archivado en Odoo
    se archiva allí.

    Cada módulo añade lo suyo heredando los `_sync_*` (Inventario, los almacenes
    y las series; Flota, los camiones con su chófer y los autónomos).
    """
    _name = 'decafirma.sync.wizard'
    _description = 'DecaFirma: puesta a punto'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    paso = fields.Selection([('elegir', 'Qué se manda'), ('ver', 'Ver antes de mandar'), ('hecho', 'Hecho')],
                            default='elegir', required=True)

    con_empresa = fields.Boolean('Datos de la empresa', default=True,
                                 help='Razón social, NIF, dirección, teléfono y correo como en Odoo. '
                                      'Lo que en Odoo está vacío no se manda (no se borra en DecaFirma).')
    con_ajustes = fields.Boolean('Ajustes de emisión', default=True)
    con_series = fields.Boolean('Series de albarán', default=False,
                                help='Las de los albaranes de salida, con su formato y por donde va su contador '
                                     '(DecaFirma solo lo sube: nunca repite números).')
    con_clientes = fields.Boolean('Clientes', default=True, help='Los que tienen NIF: sin él, DecaFirma no los da de alta.')
    con_lugares = fields.Boolean('Lugares', default=True, help='Las direcciones de entrega de los clientes.')
    con_oficina = fields.Boolean('Gente de oficina', default=False, help='Los usuarios internos de Odoo, con su rol.')
    invitar = fields.Boolean('Invitarles ahora', default=False,
                             help='DecaFirma manda la invitación a quien tenga correo y aún no tenga cuenta. '
                                  'Si no, se les invita luego desde DecaFirma > Equipo.')

    cuanto_empresa = fields.Char(compute='_compute_cuantos')
    cuanto_ajustes = fields.Char(compute='_compute_cuantos')
    cuanto_series = fields.Char(compute='_compute_cuantos')
    cuanto_clientes = fields.Char(compute='_compute_cuantos')
    cuanto_lugares = fields.Char(compute='_compute_cuantos')
    cuanto_oficina = fields.Char(compute='_compute_cuantos')

    resumen = fields.Html('Resumen', readonly=True, sanitize=False)

    # ------------------------------------------------------------------
    # Qué hay en Odoo
    # ------------------------------------------------------------------

    def _sync_vacio(self):
        return {'company': None, 'settings': None, 'nombres': {},
                **{t: [] for t in TIPOS}, 'fuera': {t: [] for t in TIPOS}}

    def _sync_datos(self):
        """Lo que se manda con lo marcado: {company, settings, series, customers, places, people, vehicles}
        más «fuera» (lo que no se manda y por qué) y «nombres» (externalId → cómo se llama en Odoo)."""
        self.ensure_one()
        datos = self._sync_vacio()
        if self.con_empresa:
            datos['company'] = self._sync_empresa() or None
        if self.con_ajustes:
            datos['settings'] = self._sync_ajustes() or None
        if self.con_series:
            self._sync_series(datos)
        if self.con_clientes:
            self._sync_clientes(datos)
        if self.con_lugares:
            self._sync_lugares(datos)
        if self.con_oficina:
            self._sync_oficina(datos)
        self._sync_personas(datos)
        self._sync_vaciados(datos)
        self._sync_archivados(datos)
        for p in datos['people']:
            p.pop('invite', None)
            if self.invitar and p.get('email') and not p.get('archived'):
                p['invite'] = True
        return datos

    def _sync_vaciados(self, datos):
        """Lo que se mandó la otra vez y ahora en Odoo está vacío va como null: DecaFirma lo vacía (manda Odoo).
        Lo que nunca vino de Odoo no se toca: lo vacío en Odoo no borra lo que se puso a mano en DecaFirma."""
        links = {(lk.tipo, lk.external_id): lk.ultimo or {} for lk in self.env['decafirma.sync.link'].sudo().search(
            [('company_id', '=', self.company_id.id)])}
        for tipo in TIPOS:
            for item in datos[tipo]:
                for k, v in links.get((tipo, item['externalId']), {}).items():
                    if k not in item and k not in SI_NO and k != 'nextNumber' and v not in (None, ''):
                        item[k] = None

    def _sync_anadir(self, datos, tipo, rec, item, nombre=None):
        """Un elemento a mandar (una vez: el mismo registro no va dos veces)."""
        item = limpio(dict(item, externalId=item.get('externalId') or ext_id(rec)))
        if any(x['externalId'] == item['externalId'] for x in datos[tipo]):
            return
        datos[tipo].append(item)
        datos['nombres'][item['externalId']] = nombre or item.get('name') or item.get('plate') or rec.display_name

    def _sync_fuera(self, datos, tipo, nombre, motivo):
        datos['fuera'][tipo].append((nombre, motivo))

    @staticmethod
    def _sync_direccion(partner):
        """Dirección, código postal, población y provincia de un contacto, como los pide DecaFirma."""
        return {
            'address': ', '.join(filter(None, [partner.street, partner.street2])) or None,
            'postcode': partner.zip or None,
            'city': partner.city or None,
            'province': partner.state_id.name or None,
        }

    @staticmethod
    def _sync_telefono(partner):
        # «mobile» ya no está en las versiones nuevas de Odoo.
        return partner.phone or (partner.mobile if 'mobile' in partner._fields else None) or None

    def _sync_empresa(self):
        c = self.company_id.sudo()
        p = c.partner_id
        return limpio({'legalName': c.name, 'taxId': nif(c.vat), 'phone': self._sync_telefono(p), 'email': p.email,
                       **self._sync_direccion(p)})

    def _sync_ajustes(self):
        """Los ajustes de emisión que tienen su igual en DecaFirma. Otros módulos añaden los suyos."""
        c = self.company_id.sudo()
        # Con el número de Odoo (o firmando el albarán de Odoo), el número del albarán lo pone quien emite.
        ajustes = {'manualDeliveryNoteNumber': bool(c.decafirma_numero_odoo or c.decafirma_pdf_odoo)}
        if c.decafirma_formato_albaran:
            ajustes['deliveryNoteFormat'] = c.decafirma_formato_albaran.strip().upper()
        return ajustes

    def _sync_series(self, datos):
        """Las series del albarán. En el conector solo no hay: las añade Inventario."""

    def _sync_clientes_candidatos(self):
        """Los clientes de la empresa: con «customer_rank» (Facturación/Ventas) y, con Inventario, a quien se le entrega."""
        Partner = self.env['res.partner'].sudo()
        if 'customer_rank' not in Partner._fields:
            return Partner
        return Partner.search([('customer_rank', '>', 0), ('parent_id', '=', False),
                               ('company_id', 'in', [False, self.company_id.id])])

    def _sync_clientes_todos(self):
        """Los clientes (la ficha principal, no sus contactos), sin la propia empresa. Los que no tienen NIF
        no se mandan: se dice. (En Odoo 20 «empresa» ya es tener NIF: por eso no se mira is_company.)"""
        propios = self.env['res.company'].sudo().search([]).partner_id
        return (self._sync_clientes_candidatos() - propios).filtered(lambda p: p.active and not p.parent_id)

    def _sync_clientes(self, datos):
        for p in self._sync_clientes_todos():
            if not nif(p.vat):
                self._sync_fuera(datos, 'customers', p.display_name, _('Sin NIF.'))
                continue
            self._sync_anadir(datos, 'customers', p, {
                'name': p.name, 'taxId': nif(p.vat), 'phone': self._sync_telefono(p), 'email': p.email,
                **self._sync_direccion(p)})

    def _sync_lugar(self, datos, rec, nombre, partner):
        if not partner or not partner.city:
            self._sync_fuera(datos, 'places', nombre, _('Sin población.'))
            return
        self._sync_anadir(datos, 'places', rec, {'name': nombre, 'phone': self._sync_telefono(partner),
                                                 **self._sync_direccion(partner)})

    def _sync_lugares(self, datos):
        """Las direcciones de entrega de los clientes. Inventario añade los almacenes."""
        for cliente in self._sync_clientes_todos():
            for d in cliente.child_ids.filtered(lambda c: c.type == 'delivery'):
                nombre = f'{cliente.name} - {d.name}' if d.name else cliente.name
                self._sync_lugar(datos, d, nombre, d)

    def _sync_usuarios(self):
        return self.env['res.users'].sudo().search([('share', '=', False), ('company_ids', 'in', self.company_id.id)]) \
            .filtered(lambda u: not u._is_superuser())

    def _sync_oficina(self, datos):
        """Los usuarios internos, con su rol de DecaFirma: Responsable o administrador, «admin»;
        Usuario, «issuer» (emite y corrige); el resto, «viewer»."""
        for u in self._sync_usuarios():
            emite = u.has_group('decafirma.group_decafirma_user')
            rol = 'admin' if u.has_group(GRUPO_RESPONSABLE) else ('issuer' if emite else 'viewer')
            correo = u.email or (u.login if '@' in (u.login or '') else None)
            self._sync_anadir(datos, 'people', u, {
                'kind': 'office', 'name': u.name, 'taxId': nif(u.partner_id.vat), 'phone': self._sync_telefono(u.partner_id),
                'email': correo, 'role': rol, 'issuesDeca': emite, 'issuesDeliveryNote': emite,
                'editsDeca': emite, 'editsDeliveryNote': emite})

    def _sync_personas(self, datos):
        """Chóferes y transportistas: los añade Flota."""

    def _sync_archivados(self, datos):
        """Lo que se mandó otra vez y en Odoo ya está archivado o borrado: se archiva en DecaFirma.

        Solo en lo marcado. Lo que deja de cumplir (un cliente que pierde el NIF) no se archiva: deja de mandarse.
        """
        mandados = {x['externalId'] for t in TIPOS for x in datos[t]}
        for tipo in self._sync_tipos_marcados():
            links = self.env['decafirma.sync.link'].sudo().search([('company_id', '=', self.company_id.id), ('tipo', '=', tipo)])
            for link in links.filtered(lambda lk: lk.external_id not in mandados):
                rec = link._registro()
                if rec is None or (rec and ('active' not in rec._fields or rec.active)):
                    continue  # no es de Odoo, o sigue vivo
                if not self._sync_es_suyo(tipo, link.external_id):
                    continue
                item = dict(link.ultimo or {}, externalId=link.external_id, archived=True)
                item.pop('invite', None)
                datos[tipo].append(item)
                datos['nombres'][link.external_id] = item.get('name') or item.get('plate') or link.external_id

    def _sync_tipos_marcados(self):
        marcados = []
        if self.con_series:
            marcados.append('series')
        if self.con_clientes:
            marcados.append('customers')
        if self.con_lugares:
            marcados.append('places')
        if self.con_oficina:
            marcados.append('people')
        return marcados

    def _sync_es_suyo(self, tipo, external_id):
        """Si un elemento archivado es de lo marcado (las personas de oficina son usuarios; Flota añade las suyas)."""
        if tipo == 'people':
            return self.con_oficina and external_id.startswith('odoo:res.users:')
        return True

    # ------------------------------------------------------------------
    # Cuántos hay (el primer paso)
    # ------------------------------------------------------------------

    def _sync_cuenta(self, datos, tipo, texto=None):
        n, fuera = len(datos[tipo]), len(datos['fuera'][tipo])
        t = texto or _('%s para mandar', n)
        if fuera:
            t += ' ' + _('(%s no se mandan)', fuera)
        return t

    @api.depends('company_id')
    def _compute_cuantos(self):
        for w in self:
            d = w._sync_vacio()
            empresa = w._sync_empresa()
            w._sync_series(d)
            w._sync_clientes(d)
            w._sync_lugares(d)
            w._sync_oficina(d)
            w.cuanto_empresa = _('%s datos en Odoo', len(empresa))
            ajustes = w._sync_ajustes()
            w.cuanto_ajustes = _('%s ajustes', len(ajustes))
            w.cuanto_series = w._sync_cuenta(d, 'series')
            w.cuanto_clientes = w._sync_cuenta(d, 'customers', _('%s con NIF', len(d['customers'])))
            w.cuanto_lugares = w._sync_cuenta(d, 'places')
            w.cuanto_oficina = w._sync_cuenta(d, 'people', _('%s usuarios', len(d['people'])))

    # ------------------------------------------------------------------
    # Mandar, por tandas
    # ------------------------------------------------------------------

    def _sync_tandas(self, datos, dry_run):
        """Los cuerpos de POST /sync, de hasta 500 elementos. Un vehículo va en la misma tanda que su conductor:
        así también en «ver antes de mandar» (que no guarda nada) DecaFirma sabe de quién es."""
        unidades = [[(t, x)] for t in ('series', 'customers', 'places') for x in datos[t]]
        vehiculos = list(datos['vehicles'])
        for p in datos['people']:
            suyos = [v for v in vehiculos if v.get('driverExternalId') == p['externalId']]
            vehiculos = [v for v in vehiculos if v not in suyos]
            unidades.append([('people', p)] + [('vehicles', v) for v in suyos])
        unidades += [[('vehicles', v)] for v in vehiculos]

        tandas, actual, n = [], {}, 0
        for unidad in unidades:
            if actual and n + len(unidad) > MAX_POR_LLAMADA:
                tandas.append(actual)
                actual, n = {}, 0
            for tipo, x in unidad:
                actual.setdefault(tipo, []).append(x)
            n += len(unidad)
        if actual or not tandas:
            tandas.append(actual)
        tandas[0] = dict(tandas[0], **{k: datos[k] for k in ('company', 'settings') if datos.get(k)})
        return [dict(t, dryRun=dry_run) for t in tandas if set(t) - {'dryRun'}]

    @staticmethod
    def _sync_juntar(respuestas):
        """Las respuestas de las tandas, en una."""
        total = {'company': {'updated': [], 'skipped': []}, 'settings': {'updated': [], 'skipped': []}}
        for r in respuestas:
            empresa = r.get('company') or {}
            total['company']['updated'] += [f for f in (empresa.get('updated') or [])
                                            if f not in total['company']['updated']]
            total['company']['skipped'] += empresa.get('skipped') or []
            ajustes = r.get('settings') or {}
            total['settings']['updated'] += [f for f in (ajustes.get('updated') or []) if f not in total['settings']['updated']]
            total['settings']['skipped'] += ajustes.get('skipped') or []
            for tipo in TIPOS:
                parte = r.get(tipo) or {}
                t = total.setdefault(tipo, {'created': 0, 'updated': 0, 'unchanged': 0, 'archived': 0, 'invited': 0,
                                            'skipped': [], 'ids': {}})
                for k in ('created', 'updated', 'unchanged', 'archived', 'invited'):
                    t[k] += parte.get(k) or 0
                t['skipped'] += parte.get('skipped') or []
                t['ids'].update(parte.get('ids') or {})
        return total

    def _sync_mandar(self, datos, dry_run):
        tandas = self._sync_tandas(datos, dry_run)
        if not tandas:
            raise UserError(_('No hay nada que mandar: marca algo (o no hay nada en Odoo de lo marcado).'))
        client = DecafirmaClient(self.company_id)
        respuestas = []
        try:
            for cuerpo in tandas:
                _code, data = client.sincronizar(cuerpo)
                respuestas.append(data or {})
        except DecafirmaError as e:
            if respuestas and not dry_run:
                raise UserError(_('%(error)s Se han mandado %(hechas)s de %(total)s tandas: vuelve a mandarlo, '
                                  'lo que ya está saldrá sin cambios.', error=str(e), hechas=len(respuestas),
                                  total=len(tandas))) from e
            if '(403)' in str(e):
                raise UserError(_('%s Los datos de la empresa, los ajustes, las series y la gente piden una clave '
                                  'creada por un administrador de DecaFirma; desmárcalos o cambia la clave.', str(e))) from e
            raise UserError(str(e)) from e
        return self._sync_juntar(respuestas)

    def _sync_guardar_ids(self, datos, total):
        """El id de DecaFirma de cada cosa, para la próxima vez. Solo se escribe lo que cambia."""
        Link = self.env['decafirma.sync.link'].sudo()
        existentes = {(lk.tipo, lk.external_id): lk for lk in Link.search([('company_id', '=', self.company_id.id)])}
        nuevos = []
        for tipo in TIPOS:
            ids = total.get(tipo, {}).get('ids') or {}
            for item in datos[tipo]:
                deca_id = ids.get(item['externalId'])
                if not deca_id:
                    continue
                ultimo = {k: v for k, v in item.items() if k != 'invite'}
                lk = existentes.get((tipo, item['externalId']))
                if not lk:
                    nuevos.append({'company_id': self.company_id.id, 'tipo': tipo, 'external_id': item['externalId'],
                                   'deca_id': deca_id, 'ultimo': ultimo})
                elif lk.deca_id != deca_id or json.dumps(lk.ultimo, sort_keys=True) != json.dumps(ultimo, sort_keys=True):
                    lk.write({'deca_id': deca_id, 'ultimo': ultimo})
        if nuevos:
            Link.create(nuevos)

    # ------------------------------------------------------------------
    # El resumen, en castellano
    # ------------------------------------------------------------------

    def _sync_resumen(self, datos, total, dry_run):
        nombres = {'series': _('Series de albarán'), 'customers': _('Clientes'), 'places': _('Lugares'),
                   'people': _('Personas'), 'vehicles': _('Vehículos')}
        campos_empresa = {'legalName': _('razón social'), 'tradeName': _('nombre comercial'), 'taxId': _('NIF'),
                          'phone': _('teléfono'), 'email': _('correo'), 'address': _('dirección'),
                          'postcode': _('código postal'), 'city': _('población'), 'province': _('provincia')}
        campos_ajustes = {'manualDeliveryNoteNumber': _('número del albarán a mano'),
                          'deliveryNoteFormat': _('formato del número del albarán'),
                          'askForAttachments': _('pedir los papeles del porte'), 'hourlyServices': _('servicios por horas')}
        h = Markup('')
        if dry_run:
            h += Markup('<p>%s</p>') % _('Esto es lo que haría DecaFirma. Aún no se ha guardado nada.')
        else:
            h += Markup('<p><strong>%s</strong></p>') % _('Hecho: DecaFirma ya está como Odoo.')
        filas = Markup('')
        omitidos = Markup('')
        if datos.get('company'):
            orden = list(campos_empresa)
            hechos = [campos_empresa.get(f, f) for f in sorted(total['company']['updated'], key=lambda f: orden.index(f) if f in orden else 99)]
            texto = (_('cambia: %s', ', '.join(hechos)) if dry_run else _('cambiado: %s', ', '.join(hechos))) if hechos \
                else _('sin cambios')
            filas += Markup('<tr><td>%s</td><td colspan="5">%s</td></tr>') % (_('Datos de la empresa'), texto)
            for s in total['company']['skipped']:
                omitidos += Markup('<li>%s: %s</li>') % (_('Datos de la empresa'), omitido(s))
        if datos.get('settings'):
            cambian = [campos_ajustes.get(f, f) for f in total['settings']['updated']]
            texto = (_('cambia: %s', ', '.join(cambian)) if dry_run else _('cambiado: %s', ', '.join(cambian))) if cambian \
                else _('sin cambios')
            filas += Markup('<tr><td>%s</td><td colspan="5">%s</td></tr>') % (_('Ajustes de emisión'), texto)
            for s in total['settings']['skipped']:
                omitidos += Markup('<li>%s: %s</li>') % (_('Ajustes de emisión'), omitido(s))
        for tipo in TIPOS:
            t = total.get(tipo) or {}
            fuera = datos['fuera'][tipo]
            if not datos[tipo] and not fuera:
                continue
            no = len(t.get('skipped') or []) + len(fuera)
            filas += Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
                nombres[tipo], t.get('created', 0), t.get('updated', 0), t.get('unchanged', 0), t.get('archived', 0), no)
            for s in t.get('skipped') or []:
                nombre = datos['nombres'].get(s.get('externalId')) or s.get('externalId') or '—'
                omitidos += Markup('<li>%s: <strong>%s</strong>. %s</li>') % (nombres[tipo], nombre, omitido(s))
            for nombre, motivo in fuera:
                omitidos += Markup('<li>%s: <strong>%s</strong>. %s</li>') % (nombres[tipo], nombre, motivo)
            if t.get('invited'):
                omitidos += Markup('<li>%s</li>') % (_('%s invitaciones', t['invited']) if not dry_run
                                                      else _('Se invitaría a %s personas', t['invited']))
        cabecera = (_('Se crean'), _('Se actualizan'), _('Sin cambios'), _('Se archivan'), _('No se mandan')) if dry_run \
            else (_('Creados'), _('Actualizados'), _('Sin cambios'), _('Archivados'), _('No mandados'))
        h += Markup('<table class="table table-sm"><thead><tr><th></th>%s</tr></thead><tbody>%s</tbody></table>') % (
            Markup('').join(Markup('<th>%s</th>') % c for c in cabecera), filas)
        if omitidos:
            h += Markup('<p>%s</p><ul>%s</ul>') % (_('Lo que no se manda, y por qué:'), omitidos)
        return h

    # ------------------------------------------------------------------
    # Los botones
    # ------------------------------------------------------------------

    def _reabrir(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new', 'name': _('Puesta a punto de DecaFirma')}

    def action_ver(self):
        """Paso 2: lo que haría DecaFirma, sin guardar nada (dryRun)."""
        self.ensure_one()
        comprobar_permiso(self.env, GRUPO_RESPONSABLE)
        datos = self._sync_datos()
        total = self._sync_mandar(datos, dry_run=True)
        self.write({'resumen': self._sync_resumen(datos, total, dry_run=True), 'paso': 'ver'})
        return self._reabrir()

    def action_mandar(self):
        """Paso 3: se manda de verdad, se guardan los ids y queda una nota en la empresa."""
        self.ensure_one()
        comprobar_permiso(self.env, GRUPO_RESPONSABLE)
        datos = self._sync_datos()
        total = self._sync_mandar(datos, dry_run=False)
        self._sync_guardar_ids(datos, total)
        resumen = self._sync_resumen(datos, total, dry_run=False)
        self.write({'resumen': resumen, 'paso': 'hecho'})
        self.company_id.partner_id.sudo().message_post(
            body=Markup('<p><strong>%s</strong></p>') % _('Puesta a punto de DecaFirma') + resumen)
        return self._reabrir()

    def action_volver(self):
        self.ensure_one()
        self.write({'paso': 'elegir', 'resumen': False})
        return self._reabrir()
