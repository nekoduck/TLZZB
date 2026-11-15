【人物】{{ name }}
{% if intro %}【来历】{{ intro }}
{% endif %}
{% if values %}【性情】{{ values | join("；") }}
{% endif %}
{% if drives %}【所求】{{ drives | join("；") }}
{% endif %}
{% if bottom_lines %}【底线】{{ bottom_lines | join("；") }}
{% endif %}
{% if choices %}【遇事的做法】
{% for ch in choices %}
- {{ ch }}
{% endfor %}
{% endif %}
