# Resource Monitor para Omarchy

Um monitor do sistema para a barra do [Omarchy](https://omarchy.org): CPU, memória, rede e
E/S de disco ao vivo, uma lista de processos em que você pode agir, discos (iPhones e iPads
conectados inclusive), sensores de temperatura e as transferências de arquivos rodando em
segundo plano (`rsync`, `cp`, downloads, uploads para um remoto na nuvem, backups de
iPhone/iPad e mais) com velocidade, progresso e tempo restante.

🇺🇸 [Read in English](README.md)

<p align="center"><img src="docs/demo.gif" width="480" alt="A aba Transferências: um script de backup copiando fotos com rsync, o progresso geral e o do arquivo avançando, depois o lote inteiro pausado e retomado"></p>

## O que você ganha

- **Rótulo na barra** com CPU e RAM, em quatro modos: compacto, detalhado (acrescenta
  temperatura e memória usada), completo (acrescenta a velocidade da rede) ou só o ícone.
  Clique com o botão direito no rótulo para alternar.
- **Geral**: CPU (gráfico de histórico, carga e frequência por núcleo), memória e swap,
  gráficos de rede e de E/S de disco, e um cartão de Transferências. Clique no cartão para
  abrir a aba Transferências.
- **Processos**: os 10 que mais usam CPU ou memória. Encerre um (SIGTERM) com um clique ou
  <kbd>x</kbd>, ou abra o `btop`.
- **Discos**: todo sistema de arquivos montado, com o espaço livre. Clique num disco para
  abri-lo no gerenciador de arquivos (`xdg-open`). **iPhones e iPads** conectados aparecem
  com o armazenamento quando o `libimobiledevice` está instalado (eles não têm pasta para
  abrir).
- **Sensores**: temperaturas, ventoinhas, consumo da bateria, modelo da CPU, kernel, clock
  da GPU, carga, trocas de contexto e tempo ligado.
- **Transferências**: `rsync`, `rclone`, `scp`, `sftp`, `cp`, `mv`, `dd`, `pv`, `curl`,
  `wget`, `aria2c`, `yt-dlp`, `git`, `idevicebackup2`, `restic`, `borg`, `btrfs`,
  `pacman`/`yay`/`paru`, `tar`, `zstd` e `ffmpeg` iniciados por você. Cada uma mostra a
  velocidade, o arquivo sendo copiado, o progresso geral e o tempo restante quando o tamanho
  total é conhecido, e pode ser pausada, retomada, cancelada, aberta ou copiada como comando.
  Uma notificação avisa quando uma transferência longa termina. Transferências de ou para
  outra máquina ou um remoto na nuvem (`rclone copy dir gdrive:x`, `rsync dir host:x`, `scp`)
  entram na lista, marcadas com ↑ ou ↓, com a velocidade medida do lado da rede. Os caminhos
  aparecem completos (os relativos resolvidos a partir da pasta de trabalho do comando), o
  tamanho total de uma pasta é calculado em segundo plano com `du`, e um cartão no topo soma
  tudo. Uma transferência iniciada por um script (`bash backup.sh`, um script Python, `make`)
  aparece como uma etapa desse **lote**, com o caminho do script, há quanto tempo ele roda e
  quanto já moveu; **Pausar lote** e **Cancelar lote** agem sobre o script inteiro (cancelar
  uma transferência só encerra aquela etapa, e o script segue para a próxima).
- **Backups de iPhone/iPad** (`idevicebackup2 backup`): o tamanho do backup não é conhecido
  de antemão, então o progresso e o tempo restante são estimados pelo espaço usado no
  aparelho e marcados com **≈** e *(estimativa)*.
- Segue o tema do Omarchy ao vivo e fala inglês e português (Brasil e Portugal).

<p align="center">
  <img src="docs/overview.png" width="270" alt="A aba Geral: CPU com barras por núcleo, memória, rede, E/S de disco e as transferências ativas">
  <img src="docs/transfers-pt-BR.png" width="270" alt="A aba Transferências em português: o total somado e o script do lote, um rsync com progresso geral e por arquivo, e um backup de iPhone com progresso estimado">
  <img src="docs/processes.png" width="270" alt="Processos ordenados por CPU">
</p>
<p align="center">
  <img src="docs/disks.png" width="270" alt="Discos, com um iPhone conectado">
  <img src="docs/sensors.png" width="270" alt="Sensores e hardware">
</p>

*(O GIF e as capturas são gerados a partir de dados fictícios pelo ambiente de
desenvolvimento, veja [Desenvolvimento](#desenvolvimento).)*

## Instalação

```bash
omarchy plugin add https://github.com/gusthanks/omarchy-resources.git --enable
omarchy restart shell
```

Atualize com `omarchy plugin update gusthx.resources --yes` e depois
`omarchy restart shell`. Desinstale com `omarchy plugin remove gusthx.resources`. O plugin
não guarda arquivos próprios fora do diretório de execução (veja [Privacidade](#privacidade)),
que é limpo ao sair da sessão.

### Requisitos

Obrigatórios: `python3` (transferências) e `procps` (`ps`), presentes em toda instalação do
Omarchy. Opcionais, cada um acrescenta um recurso:

| Pacote | Acrescenta |
|---|---|
| `libimobiledevice` | armazenamento do iPhone/iPad na aba Discos, estimativa de tamanho dos backups do `idevicebackup2` |
| `btop` | o botão *btop* e a tecla <kbd>b</kbd> |
| `lm_sensors` / drivers hwmon | mais itens na aba Sensores |
| `xdg-utils` e um gerenciador de arquivos | abrir discos e pastas de transferências |
| `wl-clipboard` | *Copiar comando* |
| `libnotify` (`notify-send`) | a notificação de "transferência concluída" |

## Uso

| No rótulo da barra | Faz |
|---|---|
| Clique esquerdo | Abre ou fecha o popup |
| Clique do meio | Abre o `btop` |
| Clique direito | Próximo modo da barra (compacto, detalhado, completo, ícone) |

No popup:

| Tecla | Faz |
|:---:|:---|
| <kbd>1</kbd>–<kbd>5</kbd>, <kbd>←</kbd>/<kbd>→</kbd> | Troca de aba (Geral, Processos, Discos, Sensores, Transferências) |
| <kbd>↑</kbd>/<kbd>↓</kbd> | Seleciona um processo ou transferência; rola nas outras abas |
| <kbd>PgUp</kbd>/<kbd>PgDn</kbd>, <kbd>Home</kbd>/<kbd>End</kbd> | Rola a aba |
| <kbd>s</kbd> | Ordena os processos por CPU ou memória |
| <kbd>x</kbd> / <kbd>Delete</kbd> | Encerra o processo selecionado, ou cancela a transferência selecionada |
| <kbd>p</kbd> | Pausa ou retoma a transferência selecionada |
| <kbd>o</kbd> | Abre o destino da transferência selecionada |
| <kbd>c</kbd> | Copia o comando da transferência selecionada |
| <kbd>b</kbd> | Abre o `btop` |
| <kbd>m</kbd> | Próximo modo da barra |
| <kbd>r</kbd> | Atualiza agora |
| <kbd>Esc</kbd> | Fecha |

De um atalho de teclado ou script:

```bash
omarchy-shell gusthx.resources toggle   # também: open, close, cycleMode
```

O modo da barra fica salvo na entrada do widget em `~/.config/omarchy/shell.json`
(`"barMode": "compact" | "detailed" | "full" | "icon"`).

## Privacidade

Nada sai da sua máquina: sem requisições de rede, sem telemetria, sem logs.

O que ele **lê**: `/proc` (contadores de CPU, memória, rede e disco; dos seus próprios
processos também a linha de comando, os arquivos abertos em `/proc/<pid>/fd` e os contadores
de E/S, para detectar transferências e o arquivo sendo copiado), `/sys` (temperaturas,
frequências, ventoinhas, bateria), a saída de `df` e `ps` e, quando o `libimobiledevice` está
instalado, o nome, o modelo e o armazenamento dos iPhones/iPads conectados (`idevice_id`,
`ideviceinfo`).

O que ele **grava**, só no seu diretório de execução por usuário (`$XDG_RUNTIME_DIR`,
normalmente `/run/user/<uid>`, legível só por você, limpo ao sair da sessão):

- `gusthx-resources-transfers.json`: as transferências vistas na última leitura (comandos,
  caminhos, contadores de bytes) e as últimas concluídas, para calcular velocidades.
- `gusthx-resources-size-<pid>-<start>`: o tamanho que o `du` encontrou para as pastas que
  uma transferência em andamento lê; apagado quando a transferência termina.
- `gusthx-resources-ios.tsv`: iPhones/iPads conectados (nome, modelo, armazenamento, UDID),
  guardado por 30 segundos.

O que ele **faz** só quando você pede: envia SIGTERM a um processo, SIGSTOP/SIGCONT/SIGTERM
a uma transferência, abre pastas com `xdg-open`, copia um comando para a área de
transferência (`wl-copy`), abre o `btop`. Quando uma transferência longa termina, mostra uma
notificação na área de trabalho.

## Desenvolvimento

Trabalhe num clone deste repositório, nunca na cópia instalada em
`~/.config/omarchy/plugins/` (salvar lá recarrega a sua barra). O `dev/run.sh` inicia uma
instância **separada** do Quickshell com as bibliotecas `Commons`/`Ui` do Omarchy, este
repositório e dados fictícios de `dev/mock/`: ele nunca lê o seu sistema, não roda os
coletores e não mexe no shell em execução. As capturas são renderizadas fora da tela, então
nada aparece no monitor.

```bash
dev/run.sh check     # compila todos os tipos QML, Panel.qml incluído
dev/run.sh test      # testes de Model.js, i18n e coletores (node --test + python3 unittest, sem pacotes)
dev/run.sh docs      # docs/overview.png, processes.png, disks.png, sensors.png, transfers.png, preview.png, demo.gif
dev/run.sh gif       # só docs/demo.gif: uma transferência avançando, o lote pausado e retomado (ffmpeg)
dev/run.sh preview   # o popup numa janela, com dados fictícios
LANGUAGE=pt-BR dev/run.sh transfers   # docs/transfers-pt-BR.png
omarchy plugin validate .   # de um checkout limpo (uma node_modules/ local faz falhar)
```

```
Panel.qml              rótulo da barra, janela do popup, IPC
Monitor.qml            amostragem, coletores e ações
ResourcesView.qml      o conteúdo do popup, alimentado pelo Monitor.qml ou pelo harness
Model.js               análise e formatação puras
collect-details.sh     CPU, sensores, discos, iPhones/iPads, processos principais
collect-transfers.py   detecção de transferências, lotes e ações
i18n/                  textos (en, pt-BR, pt), lidos via Commons.Translations
dev/                   harness isolado (run.sh, Harness.qml), dados fictícios, testes, deploy.sh
docs/                  imagens geradas pelo dev/run.sh
```

Quando tudo estiver commitado e testado, instale na sua própria barra uma vez:

```bash
dev/deploy.sh   # valida, compila, instala o HEAD no plugin em uso e reinicia o shell uma vez
```

Se a pasta em uso for uma cópia simples e não um checkout git, o `deploy.sh` primeiro a move
para `~/Work/backups/gusthx.resources.<timestamp>` e a substitui por um checkout deste
repositório. O [AGENTS.md](AGENTS.md) e o `CLAUDE.md` têm as regras que agentes de IA (e
pessoas) seguem aqui.

## Mudanças

Veja o [CHANGELOG.md](CHANGELOG.md) (em inglês).

## Licença

MIT © Gustavo Costa (gusthx). Veja [LICENSE](LICENSE).
