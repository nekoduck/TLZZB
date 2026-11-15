## 任务：P4 人物卡：{{ name }}（{{ id }}，{{ '有魂角色' if tier == 'soul' else '有名角色' }}）

只写开局那一刻的这个人：性情、口吻、底线、当时知道的事。原著里他此后的经历不进卡片，也不写「将来会怎样」。

填 `cards`，只一张：

- `id`：{{ id }}。
- `card_yaml`：characters/{{ id }}.yaml 的全文（格式见 system），不含 `voice.samples` 与 `choices`（这两样分开填）。以下面现有的卡为底，对照原文改正。`self_intro`、`values`、`habits` 用自己的话写。
- `voice_samples`：原文里他自己说的话，逐字照抄连续的一段（引号里的话，不含引号），各带段落编号。{{ '挑最能显出他口吻的 3 到 6 句' if tier == 'soul' else '至多 3 句' }}。对不上原文的会被丢掉。
- `choices`：{% if tier == 'soul' %}原著里他做过的、最能显出性情的几次抉择（3 到 6 次），`para` 写段落编号。`situation` 写成一类处境、`did` 写他的做法，都用自己的话，不点出具体的人名、地名，也不透露此后的情节：抉择只说明「这类处境他倾向怎么做」，不规定这一局里要发生什么。{% else %}有名角色不写，留空表。{% endif %}

- `knows` 只能用下面事实表里的 id，只收开局那一刻他确实知道的事；事实表里缺了而他开局就知道的事，写进 `notes`（一句话说明事实与依据段落），由人补进事实表。
- `bottom_lines` 写他决不会做的事，拦截动作用 `blocks`（动词见 system）。
- `notes`：拿不准的地方、事实表里缺了的事；没有为空串。

他在原文里出现的段落：{{ paragraphs | join('、') }}{% if more %}（另有 {{ more }} 段未列）{% endif %}


别名：{{ aliases | join('、') if aliases else '（无）' }}

现有的卡：

```yaml
{{ seed if seed else '（还没有卡）' }}
```

事实表（id：事实）：
{% for f in facts %}
- {{ f }}
{% else %}
（空）
{% endfor %}
