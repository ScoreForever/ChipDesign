最差路径cursor_at_last_x→坐标/边界mask→first lane→地址选择→存储held_data，22级，4.821ns逻辑+15.068ns布线。候选先计算current/next-nowrap/next-wrap三组完整请求地址和mask，最后按已寄存行尾位选择，保持同周期返回/发射/下一请求。first_addr加keep保留晚选择边界，防止共享优化重新串接优先编码。需评估面积和其他来源是否成为新critical。

最终：上述三路组合spec未采用，placement仍负。选定peek注册化影子游标+请求元数据，将原22级请求路径提前，保持issue吞吐和81程序全部计数。最终路由WNS+.533/WHS+.028；最差路径转为activation BRAM→results RAMD32，12级、18.782ns。LUT18425/FF7466/DSP20/BRAM4；相对forward LUT-39 FF+203 DSP+8。CDC、上板14+5通过，详见README。
