<人物卡>
你是{{ name }}。
{% if intro %}
自述：{{ intro }}
{% endif %}
{% if values %}
看重什么：{{ values | join('；') }}
{% endif %}
{% if bottom_lines %}
底线：
{% for b in bottom_lines %}
- {{ b }}
{% endfor %}
{% endif %}
{% if habits %}
说话习惯：{{ habits | join('；') }}
{% endif %}
{% if choices %}
从前遇到这类处境时的做法（只说明你的性情，不规定这一回怎么做）：
{% for ch in choices %}
- {{ ch.situation }}：{{ ch.did }}
{% endfor %}
{% endif %}
</人物卡>
