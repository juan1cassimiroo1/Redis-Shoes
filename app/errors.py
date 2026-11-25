class ShopError(Exception):
    status = 400
    code = "erro"

    def __init__(self, msg="", **extra):
        super().__init__(msg or self.code)
        self.extra = extra


class CarrinhoVazio(ShopError):
    status, code = 409, "carrinho_vazio"


class ProdutoInexistente(ShopError):
    status, code = 404, "produto_inexistente"


class EstoqueInsuficiente(ShopError):
    status, code = 409, "estoque_insuficiente"


class CupomInvalido(ShopError):
    status, code = 422, "cupom_invalido"


class CheckoutEmAndamento(ShopError):
    status, code = 429, "checkout_em_andamento"


class PersistenciaIndisponivel(ShopError):
    status, code = 503, "persistencia_indisponivel"
