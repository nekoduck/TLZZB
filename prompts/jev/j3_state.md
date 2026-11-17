【你是谁】{{ who }}
【此刻】{{ now }}
【在场】{{ present if present else "没有旁人" }}
【刚才】{{ recent if recent else "没什么动静" }}
【你记得】
{% for m in memories %}
- {{ m }}
{% else %}
- 没有特别记得的事
{% endfor %}
【你的状态】{{ status }}
【你的打算】{{ plan }}
【出了什么事】{{ what }}
