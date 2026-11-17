【你是谁】{{ who }}
【此刻】{{ now }}
【在场】{{ present }}
【刚才】{{ recent }}
【你记得】{% if memories %}
{% for m in memories %}
·{{ m }}
{% endfor %}
{% else %}没有什么要紧的。
{% endif %}
【你的状态】{{ status }}
【你的打算】{{ plan }}
【冲你来的请求】{{ requests }}
