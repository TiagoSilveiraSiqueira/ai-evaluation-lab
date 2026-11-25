# Como contribuir

O objetivo é ensinar avaliação de um contrato estruturado com critérios transparentes e exemplos reproduzíveis. Contribuições devem manter o projeto executável com Python 3.10+ e apenas a biblioteca padrão.

## Propor um caso

1. Use serviço, IDs, números, regras e cenário fictícios. Não copie chamados, logs, recursos cloud, credenciais ou procedimentos internos.
2. Escreva evidências com origem identificada e unidades claras. Separe um fato observado de uma hipótese ou confirmação.
3. Anote categorias admissíveis, IDs exigidos, fatos numéricos, lacunas, causa e prioridade. Uma causa não nula precisa de confirmação explícita no exercício; uma prioridade não nula precisa de regra explícita.
4. Peça revisão do gabarito. A validade do JSON e a concordância entre números não comprovam que uma anotação de categoria ou lacuna é adequada.
5. Adicione ao menos uma resposta que passe e uma resposta com uma falha específica, demonstrando o check que deve detectá-la. Identifique as respostas escritas como `illustrative_fixture`.

Se o dataset mudar, atualize as duas fixtures e o resultado documentado. Não ajuste o gabarito apenas para aumentar o score de uma resposta. Mantenha ambiguidades legítimas explícitas em `admitted_categories`.

## Alterar o avaliador

Explique qual erro a mudança detecta e qual comportamento válido ela preserva. Teste ausências, tipos incorretos, duplicatas e impacto nos denominadores quando forem pertinentes à mudança. Mensagens de erro devem continuar omitindo valores de respostas inválidas e nomes de campos extras.

Evite adicionar julgamento de texto livre por regex, similaridade de palavras ou um score sem interpretação definida. Uma extensão semântica precisa ser apresentada com método e limites próprios; não deve ser anunciada como verdade factual por este avaliador.

Antes de enviar uma contribuição, execute:

```console
python -m unittest discover -s tests -v
python evaluate.py --responses examples/improved.json --dataset dataset.json --require-all --output local/reference-report.json
python evaluate.py --responses examples/baseline.json --dataset dataset.json --compare examples/improved.json --output local/comparison.json
```

Descreva o problema, o comportamento resultante, os testes e qualquer alteração do gabarito. Resultados de fixtures escritas devem ser nomeados como resultados de fixtures, sem alegar melhoria de modelo ou produtividade.

## Arquivos pessoais

Use `local/` para saídas de modelos, prompts de experimento e relatórios pessoais. A pasta está ignorada pelo Git. Ao relatar um problema publicamente, reproduza-o com dados fictícios e revise os arquivos incluídos na contribuição.
