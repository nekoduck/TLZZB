# 原文：第一至{{ last }}回

下面是原文，已切成段落。每段一行：方括号里是段落编号，后面是这一段的原文。引用原文时只引这里的文字，并给出段落编号。

{% for ch in chapters %}

〔第{{ ch.cn }}回 {{ ch.title }}〕

{% for p in ch.paragraphs %}
[{{ p.id }}] {{ p.text }}
{% endfor %}
{% endfor %}
