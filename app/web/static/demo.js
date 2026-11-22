// Kit de demonstração: limpa o campo depois de enviar e rola as conversas até o fim.
(function () {
  "use strict";
  var vistos = {};
  function rolar(lista) {
    var caixa = lista && lista.parentElement;
    if (!caixa) return;
    var total = lista.getAttribute("data-total");
    if (vistos[lista.id] !== total) {
      caixa.scrollTop = caixa.scrollHeight;
      vistos[lista.id] = total;
    }
  }
  document.addEventListener("htmx:afterRequest", function (evento) {
    var form = evento.detail.elt;
    if (form && form.hasAttribute && form.hasAttribute("data-limpar") && evento.detail.successful) {
      form.reset();
      var campo = form.querySelector("input[name=text]");
      if (campo) campo.focus();
    }
  });
  function rolarTudo() {
    document.querySelectorAll(".mensagens").forEach(rolar);
  }
  // As listas são trocadas por out-of-band a cada atualização: procura de novo no documento.
  document.addEventListener("htmx:oobAfterSwap", rolarTudo);
  document.addEventListener("htmx:afterSettle", rolarTudo);
  document.addEventListener("DOMContentLoaded", rolarTudo);
})();
