`timescale 1ns/1ps

// Lab 6 · FPGA 顶层
//   板载 100 MHz 时钟 --Clocking Wizard--> 50 MHz 系统时钟
//   时钟稳定（locked）之后才释放复位：异步置位、同步释放
module fpga_top (
    input  logic fpga_clk_i,   // 板载 100 MHz 时钟
    input  logic rst_ni,       // 复位按键，低有效
    input  logic tck_i,        // JTAG
    input  logic tms_i,
    input  logic td_i,
    output logic td_o,

    // ---------------- LED 状态指示（EGo1 高电平点亮）----------------
    output logic led_locked_o,     // Clocking Wizard 是否锁定
    output logic led_heartbeat_o,  // 心跳：clk_sys 与复位正常
    output logic led_tck_o         // JTAG tck 是否有活动
);

  // ---------------- 时钟：100 MHz -> 50 MHz ----------------
  logic clk_sys;      // 50 MHz，给整个 SoC 用
  logic clk_locked;   // 1 = 时钟已稳定

  clk_wiz_0 u_clk_wiz (
      .clk_in1  (fpga_clk_i),
      .clk_out1 (clk_sys),
      .locked   (clk_locked)
  );

  // ---------------- 复位：等时钟稳定后再释放 ----------------
  // 按下复位键或时钟还没稳定，都立刻复位
  wire async_rst_n = rst_ni & clk_locked;

  // 两级寄存器：复位来时立刻生效，撤销时跟着 clk_sys 打两拍再放开
  logic [1:0] rst_sync_q;
  always_ff @(posedge clk_sys or negedge async_rst_n) begin
    if (!async_rst_n) rst_sync_q <= 2'b00;
    else              rst_sync_q <= {rst_sync_q[0], 1'b1};
  end

  logic rst_sys_n;
  assign rst_sys_n = rst_sync_q[1];

  // ---------------- 你在 Lab 3 完成的 SoC ----------------
  my_soc_top u_soc (
      .clk_i  (clk_sys),
      .rst_ni (rst_sys_n),
      .tck_i  (tck_i),
      .tms_i  (tms_i),
      .td_i   (td_i),
      .td_o   (td_o)
  );

  // ---------------- LED 状态指示 ----------------
  // locked 灯：时钟没锁定时保持灭，这是后面一切调试的前提
  assign led_locked_o = clk_locked;

  // 心跳灯：clk_sys 下分频到约 1 Hz 翻转；用 rst_sys_n 复位，按住复位键时停闪
  //   50 MHz / 2 / 25_000_000 ≈ 1 Hz
  localparam int unsigned HeartbeatHalf = 25_000_000;  // 半个周期内的 clk_sys 个数
  logic [24:0] hb_cnt_q;
  always_ff @(posedge clk_sys) begin
    if (!rst_sys_n) begin
      hb_cnt_q        <= '0;
      led_heartbeat_o <= 1'b0;
    end else if (hb_cnt_q == HeartbeatHalf - 1) begin
      hb_cnt_q        <= '0;
      led_heartbeat_o <= ~led_heartbeat_o;
    end else begin
      hb_cnt_q        <= hb_cnt_q + 1'b1;
    end
  end

  // tck 灯：在 JTAG 时钟域计数，数满一轮翻转一次
  //   OpenOCD 连上来时 tck 会跑一阵子，灯随之变化；没接调试器时保持灭
  logic [15:0] tck_cnt_q;
  always_ff @(posedge tck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      tck_cnt_q <= '0;
      led_tck_o <= 1'b0;
    end else begin
      tck_cnt_q <= tck_cnt_q + 1'b1;
      if (tck_cnt_q == 16'hFFFF) begin
        led_tck_o <= ~led_tck_o;
      end
    end
  end

endmodule
