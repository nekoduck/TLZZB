【发生的事】
{% for f in facts %}
{{ loop.index }}. {{ f }}
{% else %}
无
{% endfor %}
【可以写的背景】{{ scene }}
【在场】{{ present }}
【称呼表】{{ names }}
【你做的事】{{ player_action }}
{% if sentence %}
【要核对的句子】{{ sentence }}
{% endif %}
{% if paragraph %}
【这段话】{{ paragraph }}
{% endif %}
