// 传输层与界面之间的薄接口：传输层装上 send，界面只调 net.send。
export const net = { send: () => {}, mode: 'live' };
