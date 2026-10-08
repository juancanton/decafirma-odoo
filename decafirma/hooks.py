def post_init_hook(env):
    """Al instalar, «DecaFirma / Usuario» para todos los usuarios internos: nadie se queda sin poder emitir
    de un día para otro. Quien administra se lo quita a quien no deba."""
    grupo = env.ref('decafirma.group_decafirma_user')
    internos = env['res.users'].with_context(active_test=False).search([('share', '=', False)])
    campo = 'users' if 'users' in grupo._fields else 'user_ids'
    grupo.write({campo: [(4, u.id) for u in internos]})
