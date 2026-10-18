from odoo import _, api, fields, models

from odoo.addons.decafirma.wizard.decafirma_sync_wizard import ext_id, nif


class DecafirmaSyncWizard(models.TransientModel):
    """Con Flota, la puesta a punto añade los camiones con su chófer y los autónomos con su camión.

    Un camión de Flota (activo y con matrícula) va con su conductor (driver_id) como chófer. Un autónomo
    es un contacto con «Transportista autónomo» marcado: va como transportista, con su camión (los de Flota
    de los que es conductor o, si no tiene, la matrícula de su ficha).
    """
    _inherit = 'decafirma.sync.wizard'

    con_camiones = fields.Boolean('Camiones con su chófer', default=True,
                                  help='Los vehículos de Flota con matrícula, y su conductor como chófer.')
    con_autonomos = fields.Boolean('Autónomos con su camión', default=True,
                                   help='Los contactos marcados como «Transportista autónomo».')
    cuanto_camiones = fields.Char(compute='_compute_cuantos_flota')
    cuanto_autonomos = fields.Char(compute='_compute_cuantos_flota')

    def _sync_autonomos(self):
        return self.env['res.partner'].sudo().search([('decafirma_autonomo', '=', True)])

    def _sync_vehiculos_flota(self):
        return self.env['fleet.vehicle'].sudo().search([('company_id', 'in', [False, self.company_id.id])])

    def _sync_persona(self, datos, partner, kind):
        self._sync_anadir(datos, 'people', partner, {
            'kind': kind, 'name': partner.name, 'taxId': nif(partner.vat), 'phone': self._sync_telefono(partner),
            'email': partner.email})

    def _sync_vehiculo(self, datos, rec, matricula, conductor, descripcion=None, tipo=None):
        if not matricula:
            self._sync_fuera(datos, 'vehicles', rec.display_name, _('Sin matrícula.'))
            return
        self._sync_anadir(datos, 'vehicles', rec, {
            'plate': matricula.replace(' ', '').replace('-', '').upper(), 'description': descripcion, 'type': tipo,
            'driverExternalId': ext_id(conductor) if conductor else None}, nombre=matricula)

    def _sync_flota(self, datos, camiones=True, autonomos=True):
        autonomos_rs = self._sync_autonomos()
        vehiculos = self._sync_vehiculos_flota()
        if autonomos:
            for a in autonomos_rs:
                self._sync_persona(datos, a, 'carrier')
                suyos = vehiculos.filtered(lambda v: v.driver_id == a)
                for v in suyos:
                    self._sync_vehiculo(datos, v, v.license_plate, a, v.model_id.display_name or None,
                                        v.category_id.name or None)
                if not suyos:
                    if a.decafirma_autonomo_matricula:
                        # Sin camión en Flota: el de su ficha, con la referencia del autónomo.
                        self._sync_vehiculo(datos, a, a.decafirma_autonomo_matricula, a)
                    else:
                        self._sync_fuera(datos, 'vehicles', a.display_name, _('Autónomo sin camión: pon su matrícula en su ficha (Ventas y compras).'))
        if camiones:
            for v in vehiculos.filtered(lambda v: v.driver_id not in autonomos_rs):
                if v.driver_id:
                    self._sync_persona(datos, v.driver_id, 'driver')
                self._sync_vehiculo(datos, v, v.license_plate, v.driver_id, v.model_id.display_name or None,
                                    v.category_id.name or None)

    def _sync_personas(self, datos):
        super()._sync_personas(datos)
        self._sync_flota(datos, camiones=self.con_camiones, autonomos=self.con_autonomos)

    def _sync_tipos_marcados(self):
        marcados = super()._sync_tipos_marcados()
        if self.con_camiones or self.con_autonomos:
            marcados += [t for t in ('people', 'vehicles') if t not in marcados]
        return marcados

    def _sync_es_suyo(self, tipo, external_id):
        if tipo in ('people', 'vehicles') and external_id.startswith(('odoo:res.partner:', 'odoo:fleet.vehicle:')):
            return self.con_camiones or self.con_autonomos
        return super()._sync_es_suyo(tipo, external_id)

    @api.depends('company_id')
    def _compute_cuantos_flota(self):
        for w in self:
            d = w._sync_vacio()
            w._sync_flota(d, autonomos=False)
            choferes = len(d['people'])
            w.cuanto_camiones = w._sync_cuenta(d, 'vehicles', _('%(camiones)s camiones, %(choferes)s chóferes',
                                                              camiones=len(d['vehicles']), choferes=choferes))
            d = w._sync_vacio()
            w._sync_flota(d, camiones=False)
            w.cuanto_autonomos = w._sync_cuenta(d, 'vehicles', _('%(autonomos)s autónomos, %(camiones)s camiones',
                                                               autonomos=len(d['people']), camiones=len(d['vehicles'])))
