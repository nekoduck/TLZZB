【此刻】{{ clock }}，{{ place }}。{% if tense %}场面紧张。{% endif %}{% if danger >= 3 %}你有性命之忧。{% elif danger == 2 %}你身处险境。{% elif danger == 1 %}周围有些敌意。{% endif %}

【在场】{% if present %}{{ present | join('｜') }}{% else %}没有旁人{% endif %}

【刚才】
{% for line in recent %}
- {{ line }}
{% else %}
- 没有什么动静
{% endfor %}
【你身上】{% if body %}{{ body | join('；') }}{% else %}没有什么不适{% endif %}

【未了的牵挂】
{% for t in threads %}
- {{ t }}
{% else %}
- 无
{% endfor %}
