from odoo import api, fields, models


class DecafirmaVehiculo(models.AbstractModel):
    """El camión y el remolque de Flota en vez de escribir la matrícula a mano.

    Al elegir el vehículo se ponen su matrícula y su conductor (el de Flota);
    el remolque, otro vehículo de Flota. La matrícula se puede seguir tocando:
    es la que va en el DeCA.
    """
    _name = 'decafirma.vehiculo.mixin'
    _description = 'Vehículos de Flota para DecaFirma'

    decafirma_vehicle_id = fields.Many2one('fleet.vehicle', 'Vehículo', copy=False,
                                           help='De Flota: pone la matrícula y el conductor.')
    decafirma_trailer_vehicle_id = fields.Many2one('fleet.vehicle', 'Remolque (Flota)', copy=False,
                                                   help='De Flota: pone la matrícula del remolque.')

    @api.onchange('decafirma_vehicle_id')
    def _onchange_decafirma_vehicle(self):
        for rec in self.filtered('decafirma_vehicle_id'):
            rec.decafirma_plate = rec.decafirma_vehicle_id.license_plate or rec.decafirma_plate
            if rec.decafirma_vehicle_id.driver_id:
                rec.decafirma_driver_id = rec.decafirma_vehicle_id.driver_id

    @api.onchange('decafirma_trailer_vehicle_id')
    def _onchange_decafirma_trailer_vehicle(self):
        for rec in self.filtered('decafirma_trailer_vehicle_id'):
            rec.decafirma_trailer = rec.decafirma_trailer_vehicle_id.license_plate or rec.decafirma_trailer

    @api.model
    def _decafirma_desde_flota(self, vals):
        """Lo mismo al crear o guardar sin pantalla (importar, otra app): lo que no venga, de Flota."""
        if vals.get('decafirma_vehicle_id'):
            v = self.env['fleet.vehicle'].browse(vals['decafirma_vehicle_id'])
            if not vals.get('decafirma_plate'):
                vals['decafirma_plate'] = v.license_plate
            if v.driver_id and not vals.get('decafirma_driver_id'):
                vals['decafirma_driver_id'] = v.driver_id.id
        if vals.get('decafirma_trailer_vehicle_id') and not vals.get('decafirma_trailer'):
            vals['decafirma_trailer'] = self.env['fleet.vehicle'].browse(vals['decafirma_trailer_vehicle_id']).license_plate
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._decafirma_desde_flota(dict(v)) for v in vals_list])

    def write(self, vals):
        return super().write(self._decafirma_desde_flota(dict(vals)))


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'decafirma.vehiculo.mixin']


class StockPickingBatch(models.Model):
    _name = 'stock.picking.batch'
    _inherit = ['stock.picking.batch', 'decafirma.vehiculo.mixin']
