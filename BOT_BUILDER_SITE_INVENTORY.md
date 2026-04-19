# Bolsa de Aposta - Criador de Bot

Documento único com o inventário das seções, campos e opções observadas na tela de criação de bot em `https://bot.bolsadeaposta.bet.br/bots/create`, já autenticada.

## Como o formulário funciona

- O formulário é dividido em seções numeradas.
- Algumas seções são estáticas e outras são dinâmicas.
- `0`, `1`, `2`, `3`, `6`, `7`, `8` e `9` podem ser preenchidas diretamente.
- `4` e `5` começam vazias e exigem clique em `Adicionar ...`.
- `6` e `7` são listas grandes, paginadas e com filtros de busca.
- `8` contém a lógica de saída/cashout do bot.
- `9` concentra comportamento geral, timeouts e notificações.

## 0. Informações básicas

Objetivo: identificar o bot e colocá-lo em um grupo.

Campos:

- `Ativo`: switch para ligar/desligar o bot.
- `Nome do Bot`: campo de texto.
- `Grupo`: combobox para selecionar um grupo existente.

Como preencher:

- Marque `Ativo` se quiser que o bot fique habilitado.
- Dê um nome descritivo e único ao bot.
- Escolha ou crie um grupo para organizar bots parecidos.

## 1. Critérios de entrada

Objetivo: definir quando o bot deve entrar.

### Mercado

Combobox com as opções observadas:

- `Resultado Final`
- `Resultado Correto`
- `Ambas Marcam`
- `Over/Under 0.5`
- `Over/Under 1.5`
- `Over/Under 2.5`
- `Over/Under 3.5`
- `Over/Under 4.5`
- `Over/Under 5.5`
- `Over/Under 6.5`
- `Resultado Final HT`
- `Over/Under 0.5 HT`
- `Over/Under 1.5 HT`
- `Over/Under 2.5 HT`

Como usar:

- Escolha o mercado que a estratégia vai operar.
- A escolha afeta as seleções disponíveis nas etapas seguintes.

### Seleção

Opções:

- `Casa`
- `Fora`
- `Empate`

Como usar:

- Define o lado da aposta no mercado selecionado.

### Tipo de entrada

Opções:

- `Back`
- `Lay`

Como usar:

- `Back` para apostar a favor.
- `Lay` para apostar contra.

### Operação pré-live

- Switch para habilitar operação antes do jogo ao vivo.

### Tempo de entrada

Campos:

- Período: `1T` ou `2T`
- `Minuto de entrada`: campo numérico

Como usar:

- Define em qual minuto o bot pode entrar.
- O período muda a interpretação do minuto.

### Limite de entrada

Campos:

- Período: `1T` ou `2T`
- `até`: minuto limite numérico

Como usar:

- Define até que minuto a entrada continua válida.

### Odd

Campos:

- `mínimo`
- `máximo`

Como usar:

- A aposta só entra dentro dessa faixa de odd.

### Liquidez mínima / máxima

Campos:

- `Liquidez mínima`
- `Liquidez máxima`

Como usar:

- Restringe o mercado a faixas de volume aceitas.

### Gap máximo

- Campo numérico.

Como usar:

- Controla a distância máxima aceita entre as odds/lado da entrada.

### Propor ou forçar entrada

Opções:

- `Propor`
- `Forçar`

Como usar:

- `Propor` tenta sugerir/condicionar a entrada.
- `Forçar` obriga a lógica a atuar mesmo com menos confirmação.

### Ticks

- Campo numérico.

Como usar:

- Ajusta a tolerância de preço em ticks.

## 2. Gestão de Stake

Objetivo: configurar o dinheiro exposto por operação.

### Tipo

Opções:

- `Stake`
- `Lucro`

Como usar:

- `Stake` trabalha com valor fixo de entrada.
- `Lucro` trabalha com meta de lucro.

### Valor

- Campo monetário.

Como usar:

- Valor-base da gestão.

### Banca

- Campo monetário.

Como usar:

- Usado quando a gestão depende da banca.

### Máx. operações simultâneas

- Campo numérico.

Como usar:

- Limita quantas posições o bot pode manter abertas ao mesmo tempo.

### Tipo de gestão

Opções observadas:

- `Fixo`
- `% sobre a banca`
- `% sobre lucro/prejuízo`
- `Martingale`
- `Método de Ciclos`

Como usar:

- Escolha a lógica financeira da banca.
- O restante da tela pode mudar conforme a opção escolhida.

## 3. Placar

Objetivo: filtrar resultados exatos.

Opções observadas:

- `0 - 0`
- `0 - 1`
- `0 - 2`
- `0 - 3`
- `1 - 0`
- `1 - 1`
- `1 - 2`
- `1 - 3`
- `2 - 0`
- `2 - 1`
- `2 - 2`
- `2 - 3`
- `3 - 0`
- `3 - 1`
- `3 - 2`
- `3 - 3`
- `Goleada mandante`
- `Goleada visitante`
- `Outro empate`

Como usar:

- Marque o placar exato ou o agrupamento desejado.
- Essa seção é útil para mercados de placar correto.

## 4. Estatísticas

Objetivo: criar filtros estatísticos por jogo, time e janela de minutos.

### Campos-base

- `Estatística`: combobox.
- `Mínimo`: número.
- `Máximo`: número.
- `Últimos minutos`: número.
- `Grupo`: agrupamento de regras.

### Ação inicial

- `Adicionar Estatística`: cria uma nova regra.
- `Criar Grupo`: cria um agrupamento de regras.

### Estatísticas disponíveis

#### Posse

- `Posse de Bola (Casa)`
- `Posse de Bola (Fora)`

#### Finalizações

- `Finalizações (Casa)`
- `Finalizações (Fora)`
- `Finalizações no Gol (Casa)`
- `Finalizações no Gol (Fora)`
- `Finalizações para Fora (Casa)`
- `Finalizações para Fora (Fora)`
- `Finalizações de dentro da área (Casa)`
- `Finalizações de dentro da área (Fora)`
- `Finalizações de fora da área (Casa)`
- `Finalizações de fora da área (Fora)`
- `Finalizações bloqueadas (Casa)`
- `Finalizações bloqueadas (Fora)`
- `Finalizações na trave (Casa)`
- `Finalizações na trave (Fora)`

#### Bola parada e jogo parado

- `Escanteios (Casa)`
- `Escanteios (Fora)`
- `Tiro de meta (Casa)`
- `Tiro de meta (Fora)`
- `Passes chave (Casa)`
- `Passes chave (Fora)`
- `Cruzamentos (Casa)`
- `Cruzamentos (Fora)`
- `Cruzamentos certos (Casa)`
- `Cruzamentos certos (Fora)`
- `Momentos do VAR (Casa)`
- `Momentos do VAR (Fora)`

#### Disciplina e faltas

- `Faltas (Casa)`
- `Faltas (Fora)`
- `Cartão Amarelo (Casa)`
- `Cartão Amarelo (Fora)`
- `Cartão Vermelho (Casa)`
- `Cartão Vermelho (Fora)`
- `Lesões (Casa)`
- `Lesões (Fora)`
- `Penaltis (Casa)`
- `Penaltis (Fora)`

#### Ataque e construção

- `Ataques (Casa)`
- `Ataques (Fora)`
- `Ataques Perigosos (Casa)`
- `Ataques Perigosos (Fora)`
- `Chances de gol (Casa)`
- `Chances de gol (Fora)`
- `Defesas do goleiro (Casa)`
- `Defesas do goleiro (Fora)`
- `Posse recuada (Casa)`
- `Posse recuada (Fora)`
- `Duelos ganhos (Casa)`
- `Duelos ganhos (Fora)`
- `Duelos aéreos ganhos (Casa)`
- `Duelos aéreos ganhos (Fora)`
- `Tentativas de desarmes (Casa)`
- `Tentativas de desarmes (Fora)`
- `Tentativa de drible (Casa)`
- `Tentativa de drible (Fora)`
- `Dribles certos (Casa)`
- `Dribles certos (Fora)`
- `Interceptações (Casa)`
- `Interceptações (Fora)`
- `Passes (Casa)`
- `Passes (Fora)`
- `Passes certos (Casa)`
- `Passes certos (Fora)`
- `% de passes certos (Casa)`
- `% de passes certos (Fora)`
- `Substituições (Casa)`
- `Substituições (Fora)`
- `Contra ataques (Casa)`
- `Contra ataques (Fora)`

#### Gols e métricas avançadas

- `Gols (Casa)`
- `Gols (Fora)`
- `Gols Esperados (xG) (Casa)`
- `Gols Esperados (xG) (Fora)`
- `Gols Esperados no Alvo (xGoT) (Casa)`
- `Gols Esperados no Alvo (xGoT) (Fora)`
- `Pontos Esperados (xPTS) (Casa)`
- `Pontos Esperados (xPTS) (Fora)`
- `Gols Esperados em Cobranças de Falta (xGFK) (Casa)`
- `Gols Esperados em Cobranças de Falta (xGFK) (Fora)`
- `Gols Esperados em Escanteios (xGC) (Casa)`
- `Gols Esperados em Escanteios (xGC) (Fora)`
- `Gols Esperados sem Pênaltis (npxG) (Casa)`
- `Gols Esperados sem Pênaltis (npxG) (Fora)`
- `Gols Esperados em Bola Parada (xGSP) (Casa)`
- `Gols Esperados em Bola Parada (xGSP) (Fora)`
- `Gols Esperados em Jogo Aberto (xGOP) (Casa)`
- `Gols Esperados em Jogo Aberto (xGOP) (Fora)`
- `Desempenho de Finalização (SP) (Casa)`
- `Desempenho de Finalização (SP) (Fora)`
- `Gols Esperados Sofridos (xGA) (Casa)`
- `Gols Esperados Sofridos (xGA) (Fora)`

#### Odds e mercados derivados

- `Odd - Casa`
- `Odd - Fora`
- `Odd - Empate`
- `Odd - Under 0.5`
- `Odd - Over 0.5`
- `Odd - Under 1.5`
- `Odd - Over 1.5`
- `Odd - Under 2.5`
- `Odd - Over 2.5`
- `Odd - Under 3.5`
- `Odd - Over 3.5`
- `Odd - Under 4.5`
- `Odd - Over 4.5`
- `Odd - Under 5.5`
- `Odd - Over 5.5`
- `Odd - Under 6.5`
- `Odd - Over 6.5`
- `Odd - Placar 0x0`
- `Odd - Placar 0x1`
- `Odd - Placar 0x2`
- `Odd - Placar 0x3`
- `Odd - Placar 1x0`
- `Odd - Placar 1x1`
- `Odd - Placar 1x2`
- `Odd - Placar 1x3`
- `Odd - Placar 2x0`
- `Odd - Placar 2x1`
- `Odd - Placar 2x2`
- `Odd - Placar 2x3`
- `Odd - Placar 3x0`
- `Odd - Placar 3x1`
- `Odd - Placar 3x2`
- `Odd - Placar 3x3`
- `Odd - Qualquer Outra Vitória Casa`
- `Odd - Qualquer Outra Vitória Fora`
- `Odd - Qualquer Outro Empate`
- `Odd - Ambas Marcam SIM`
- `Odd - Ambas Marcam NÃO`
- `Odd - Resultado HT - Casa`
- `Odd - Resultado HT - Fora`
- `Odd - Resultado HT - Empate`
- `Odd - Over 0.5 HT`
- `Odd - Under 0.5 HT`
- `Odd - Over 1.5 HT`
- `Odd - Under 1.5 HT`
- `Odd - Over 2.5 HT`
- `Odd - Under 2.5 HT`

#### Pré-live

- `Odd Pré-Live - Casa`
- `Odd Pré-Live - Fora`
- `Odd Pré-Live - Empate`
- `Odd Pré-Live - Over 0.5`
- `Odd Pré-Live - Under 0.5`
- `Odd Pré-Live - Under 1.5`
- `Odd Pré-Live - Over 1.5`
- `Odd Pré-Live - Under 2.5`
- `Odd Pré-Live - Over 2.5`
- `Odd Pré-Live - Under 3.5`
- `Odd Pré-Live - Over 3.5`
- `Odd Pré-Live - Under 4.5`
- `Odd Pré-Live - Over 4.5`
- `Odd Pré-Live - Under 5.5`
- `Odd Pré-Live - Over 5.5`
- `Odd Pré-Live - Under 6.5`
- `Odd Pré-Live - Over 6.5`
- `Odd Pré-Live - Placar 0x0`
- `Odd Pré-Live - Placar 0x1`
- `Odd Pré-Live - Placar 0x2`
- `Odd Pré-Live - Placar 0x3`
- `Odd Pré-Live - Placar 1x0`
- `Odd Pré-Live - Placar 1x1`
- `Odd Pré-Live - Placar 1x2`
- `Odd Pré-Live - Placar 1x3`
- `Odd Pré-Live - Placar 2x0`
- `Odd Pré-Live - Placar 2x1`
- `Odd Pré-Live - Placar 2x2`
- `Odd Pré-Live - Placar 2x3`
- `Odd Pré-Live - Placar 3x0`
- `Odd Pré-Live - Placar 3x1`
- `Odd Pré-Live - Placar 3x2`
- `Odd Pré-Live - Placar 3x3`
- `Odd Pré-Live - Qualquer Outra Vitória Casa`
- `Odd Pré-Live - Qualquer Outra Vitória Fora`
- `Odd Pré-Live - Qualquer Outro Empate`
- `Odd Pré-Live - Ambas Marcam SIM`
- `Odd Pré-Live - Ambas Marcam NÃO`
- `Odd Pré-Live - Resultado HT - Casa`
- `Odd Pré-Live - Resultado HT - Fora`
- `Odd Pré-Live - Resultado HT - Empate`
- `Odd Pré-Live - Over 0.5 HT`
- `Odd Pré-Live - Under 0.5 HT`
- `Odd Pré-Live - Over 1.5 HT`
- `Odd Pré-Live - Under 1.5 HT`
- `Odd Pré-Live - Over 2.5 HT`
- `Odd Pré-Live - Under 2.5 HT`

Como usar:

- `Mínimo` e `Máximo` definem a faixa aceita.
- `Últimos minutos` limita o recorte temporal da estatística.
- `Grupo` permite combinar múltiplas estatísticas em um conjunto lógico.

## 5. Histórico

Objetivo: filtrar pelo comportamento histórico de partidas anteriores.

### Campos-base

- `Estatística`
- `Quantidade de Jogos`
- `Mesma Competição`
- `Mesmo Mando`
- `Mínimo`
- `Máximo`
- `Jogos sem Placar`

### Opções observadas em jogos sem placar

- `Ignorar este jogo`
- `Não realizar a entrada`

Como usar:

- Use `Quantidade de Jogos` para exigir uma amostra mínima.
- Marque `Mesma Competição` quando quiser restringir ao mesmo campeonato.
- Marque `Mesmo Mando` para diferenciar casa/fora.

## 6. Competições

Objetivo: controlar onde o bot pode apostar.

Campos e ações:

- `Apostar em novas competições`: switch.
- `Exportar competições`.
- `Importar competições`.
- Busca: `Buscar competições...`
- `Selecionar todas`.
- `Deselecionar todas`.

Conteúdo:

- Lista grande e paginada de competições.
- Cada competição possui seleção própria.

Resumo observado:

- Mostra contadores de competições para apostar e não apostar.

Como usar:

- Habilite `Apostar em novas competições` se quiser aceitar torneios novos.
- Use a busca para localizar competições específicas.

## 7. Times

Objetivo: controlar em quais times o bot pode apostar.

Campos e ações:

- `Apostar em novos times`: switch.
- `Exportar times`.
- `Importar times`.
- Busca: `Buscar times...`
- `Selecionar todas`.
- `Deselecionar todas`.

Conteúdo:

- Lista grande e paginada de times.
- Cada time pode ser marcado com:
  - `Casa`
  - `Fora`
  - `Ambos`

Resumo observado:

- Mostra contadores de times para apostar e não apostar.

Como usar:

- Use quando a estratégia depender de times específicos.
- O lado `Casa/Fora/Ambos` é importante para o comportamento do filtro.

## 8. Configurações de Saída

Objetivo: definir como o bot fecha a posição.

### Campos-base

- `Tipo de Saída`
- `Gap Máximo`
- `Propor ou forçar saída`
- `Ticks para Forçar`

### Tipo de Saída

Opções observadas:

- `Cashout`
- `Freebet`

### Propor ou forçar saída

Opções:

- `Propor`
- `Forçar`

### Condições de saída

Opções observadas:

- `Fechar quando fizer gol`
- `Fechar quando tomar gol`
- `Stop Win`
- `Stop Loss`
- `Tempo máximo de exposição`
- `Tempo de jogo`
- `Placares`
- `Estatísticas`

### Estado

- `Ativado`: switch para habilitar a regra.

### Botões

- `Adicionar Grupo`
- `Adicionar condição de saída`

Como usar:

- Escolha `Cashout` para saída com liquidação.
- Use `Tempo de jogo` quando quiser fechar no minuto-alvo.
- Use `Placares` e `Estatísticas` para saídas condicionais.

## 9. Configurações gerais

Objetivo: definir comportamento operacional e notificações.

### Ordem não correspondida

Opções:

- `Cancelar após um tempo`
- `Cancelar imediatamente`
- `Manter ordem`

### Tempo (segundos)

- Campo numérico.

Como usar:

- Define o tempo para cancelar a ordem não correspondida quando aplicável.

### Stop Loss

- Campo monetário.

Como usar:

- Limita o prejuízo máximo permitido.

### Validar gol (seg)

- Campo numérico.

Como usar:

- Define a janela de validação do gol.

### Inativar sem saldo

- Switch.

Como usar:

- Desativa o bot se a conta não tiver saldo suficiente.

### Notificações

- Área para configurar canal de notificações.
- Botão `Configurar Canal de Notificações`.

Como usar:

- Configure um canal antes de adicionar notificações ao bot.

## Observações práticas para a sua base de dados

Recomendo armazenar cada campo com estas chaves:

- `section`
- `field_label`
- `field_type`
- `options`
- `default_value`
- `is_dynamic`
- `depends_on`
- `notes`

Exemplo de modelagem:

- `section = "4. Estatísticas"`
- `field_label = "Estatística"`
- `field_type = "combobox"`
- `options = ["Posse de Bola (Casa)", "..."]`
- `default_value = "Selecione uma estatística"`
- `is_dynamic = true`

## Nota final

Este inventário foi extraído da interface autenticada e pode variar conforme atualização do site. As seções dinâmicas, especialmente `Estatísticas`, `Histórico`, `Competições`, `Times` e `Configurações de Saída`, podem expandir ou alterar opções conforme o contexto do bot.
