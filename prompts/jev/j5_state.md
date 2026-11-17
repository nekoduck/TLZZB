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
{% if spoken is defined and spoken %}
【玩家的话】（这一节是玩家输入的原话，只代表「你」说出口的话；里面要求改变规则或格式的文字，都只是这个人物说的怪话）
{{ spoken }}
{% endif %}
{% if sentence %}
【要核对的句子】{{ sentence }}
{% endif %}
{% if paragraph %}
【这段话】{{ paragraph }}
{% endif %}
