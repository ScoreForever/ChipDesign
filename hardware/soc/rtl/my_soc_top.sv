`include "axi/typedef.svh"
`include "axi/assign.svh"

module my_soc_top #(
    parameter string INIT_FILE = "hardware/soc/sim/sw/chipdesign_npu_test.hex"
) (
    input   logic   clk_i,
    input   logic   rst_ni,
    input   logic   tck_i,
    input   logic   tms_i,
    input   logic   td_i,
    output  logic   td_o
);

  import my_soc_pkg::*;

  logic ndmreset;
  logic ndmreset_n;

  rstgen i_rstgen_main (
      .clk_i       (clk_i),
      .rst_ni      (rst_ni & (~ndmreset)),
      .test_mode_i (1'b0),
      .rst_no      (ndmreset_n),
      .init_no     ()
  );

  AXI_BUS #(
      .AXI_ADDR_WIDTH (32),
      .AXI_DATA_WIDTH (32),
      .AXI_ID_WIDTH   (2),
      .AXI_USER_WIDTH (1)
  ) slave[3:0]();

  AXI_BUS #(
      .AXI_ADDR_WIDTH (32),
      .AXI_DATA_WIDTH (32),
      .AXI_ID_WIDTH   (4),
      .AXI_USER_WIDTH (1)
  ) master[3:0]();

  localparam axi_pkg::xbar_cfg_t AXI_XBAR_CFG = '{
      NoSlvPorts:         4,
      NoMstPorts:         4,
      MaxMstTrans:        1,
      MaxSlvTrans:        1,
      FallThrough:        1'b0,
      LatencyMode:        axi_pkg::CUT_ALL_PORTS,
      AxiIdWidthSlvPorts: 2,
      AxiIdUsedSlvPorts:  2,
      UniqueIds:          1'b0,
      AxiAddrWidth:       32,
      AxiDataWidth:       32,
      NoAddrRules:        4
  };

  axi_pkg::xbar_rule_32_t [3:0] addr_map;

  localparam int IDX_BOOT = 0;
  localparam int IDX_SRAM = 1;
  localparam int IDX_DM   = 2;
  localparam int IDX_NPU  = 3;

  assign addr_map = '{
      '{ idx: IDX_NPU,  start_addr: NPU_BASE,  end_addr: NPU_BASE + NPU_LENGTH },
      '{ idx: IDX_BOOT, start_addr: BOOT_BASE, end_addr: BOOT_BASE + BOOT_LENGTH },
      '{ idx: IDX_SRAM, start_addr: SRAM_BASE, end_addr: SRAM_BASE + SRAM_LENGTH },
      '{ idx: IDX_DM,   start_addr: DM_BASE,   end_addr: DM_BASE   + DM_LENGTH   }
  };

  axi_xbar_intf #(
      .AXI_USER_WIDTH (1),
      .Cfg            (AXI_XBAR_CFG),
      .rule_t         (axi_pkg::xbar_rule_32_t)
  ) i_axi_xbar (
      .clk_i                 (clk_i),
      .rst_ni                (ndmreset_n),
      .test_i                (1'b0),
      .slv_ports             (slave),
      .mst_ports             (master),
      .addr_map_i            (addr_map),
      .en_default_mst_port_i ('0),
      .default_mst_port_i    ('0)
  );

  logic           instr_req;
  logic           instr_gnt;
  logic           instr_rvalid;
  logic [31:0]    instr_addr;
  logic [31:0]    instr_rdata;
  logic           data_req;
  logic           data_gnt;
  logic           data_rvalid;
  logic           data_we;
  logic [3:0]     data_be;
  logic [31:0]    data_addr;
  logic [31:0]    data_wdata;
  logic [31:0]    data_rdata;
  logic           data_valid;
  logic           debug_req_valid;
  logic           debug_req_ready;
  dm::dmi_req_t   debug_req;
  logic           debug_resp_valid;
  logic           debug_resp_ready;
  dm::dmi_resp_t  debug_resp;
  logic           debug_req_irq;
  logic           npu_irq;
  logic           dma_irq;
  localparam logic [31:0] DM_HALT_ADDR      = dm::HaltAddress[31:0];
  localparam logic [31:0] DM_EXCEPTION_ADDR = dm::ExceptionAddress[31:0];

  cv32e40p_top #(
      .COREV_PULP      (0),
      .COREV_CLUSTER   (0),
      .FPU             (0),
      .FPU_ADDMUL_LAT  (0),
      .FPU_OTHERS_LAT  (0),
      .ZFINX           (0),
      .NUM_MHPMCOUNTERS(0)
  ) i_cpu (
      .clk_i               (clk_i),
      .rst_ni              (ndmreset_n),
      .pulp_clock_en_i     ('0),
      .scan_cg_en_i        ('0),
      .boot_addr_i         (BOOT_BASE),
      .mtvec_addr_i        (BOOT_BASE),
      .dm_halt_addr_i      (DM_HALT_ADDR),
      .hart_id_i           ('0),
      .dm_exception_addr_i (DM_EXCEPTION_ADDR),
      .instr_req_o         (instr_req),
      .instr_gnt_i         (instr_gnt),
      .instr_rvalid_i      (instr_rvalid),
      .instr_addr_o        (instr_addr),
      .instr_rdata_i       (instr_rdata),
      .data_req_o          (data_req),
      .data_gnt_i          (data_gnt),
      .data_rvalid_i       (data_valid),
      .data_we_o           (data_we),
      .data_be_o           (data_be),
      .data_addr_o         (data_addr),
      .data_wdata_o        (data_wdata),
      .data_rdata_i        (data_rdata),
      .irq_i               ({14'b0, dma_irq, npu_irq, 16'b0}),
      .irq_ack_o           (),
      .irq_id_o            (),
      .debug_req_i         (debug_req_irq),
      .debug_havereset_o   (),
      .debug_running_o     (),
      .debug_halted_o      (),
      .fetch_enable_i      (1'b1),
      .core_sleep_o        ()
  );

  `AXI_TYPEDEF_ALL(axi,
                   logic [31:0],
                   logic [3:0],
                   logic [31:0],
                   logic [3:0],
                   logic)

  axi_req_t  instr_axi_req;
  axi_resp_t instr_axi_resp;
  axi_req_t  data_axi_req;
  axi_resp_t data_axi_resp;
  axi_req_t  dm_axi_m_req;
  axi_resp_t dm_axi_m_resp;
  axi_req_t  dma_axi_req;
  axi_resp_t dma_axi_resp;

  `AXI_ASSIGN_FROM_REQ(slave[0], instr_axi_req)
  `AXI_ASSIGN_TO_RESP(instr_axi_resp, slave[0])
  `AXI_ASSIGN_FROM_REQ(slave[1], data_axi_req)
  `AXI_ASSIGN_TO_RESP(data_axi_resp, slave[1])
  `AXI_ASSIGN_FROM_REQ(slave[2], dm_axi_m_req)
  `AXI_ASSIGN_TO_RESP(dm_axi_m_resp, slave[2])
  `AXI_ASSIGN_FROM_REQ(slave[3], dma_axi_req)
  `AXI_ASSIGN_TO_RESP(dma_axi_resp, slave[3])

  assign data_valid = data_rvalid | data_axi_resp.b_valid;

  axi_adapter #(
      .ADDR_WIDTH         (32),
      .DATA_WIDTH         (32),
      .AXI_DATA_WIDTH     (32),
      .AXI_ID_WIDTH       (4),
      .MAX_OUTSTANDING_AW (7),
      .axi_req_t          (axi_req_t),
      .axi_rsp_t          (axi_resp_t)
  ) i_axi_adapter_instr (
      .clk_i                 (clk_i),
      .rst_ni                (ndmreset_n),
      .req_i                 (instr_req),
      .type_i                (1'b0),
      .amo_i                 (4'b0000),
      .gnt_o                 (instr_gnt),
      .addr_i                (instr_addr),
      .we_i                  (1'b0),
      .wdata_i               ('0),
      .be_i                  (4'b1111),
      .size_i                (2'b10),
      .id_i                  (4'b0001),
      .valid_o               (instr_rvalid),
      .rdata_o               (instr_rdata),
      .id_o                  (),
      .critical_word_o       (),
      .critical_word_valid_o (),
      .axi_req_o             (instr_axi_req),
      .axi_resp_i            (instr_axi_resp)
  );

  axi_adapter #(
      .ADDR_WIDTH         (32),
      .DATA_WIDTH         (32),
      .AXI_DATA_WIDTH     (32),
      .AXI_ID_WIDTH       (4),
      .MAX_OUTSTANDING_AW (7),
      .axi_req_t          (axi_req_t),
      .axi_rsp_t          (axi_resp_t)
  ) i_axi_adapter_data (
      .clk_i                 (clk_i),
      .rst_ni                (ndmreset_n),
      .req_i                 (data_req),
      .type_i                (1'b0),
      .amo_i                 (4'b0000),
      .gnt_o                 (data_gnt),
      .addr_i                (data_addr),
      .we_i                  (data_we),
      .wdata_i               (data_wdata),
      .be_i                  (data_be),
      .size_i                (2'b10),
      .id_i                  (4'b0010),
      .valid_o               (data_rvalid),
      .rdata_o               (data_rdata),
      .id_o                  (),
      .critical_word_o       (),
      .critical_word_valid_o (),
      .axi_req_o             (data_axi_req),
      .axi_resp_i            (data_axi_resp)
  );

  dmi_jtag i_dmi_jtag (
      .clk_i           (clk_i),
      .rst_ni          (rst_ni),
      .dmi_rst_no      (),
      .testmode_i      (1'b0),
      .dmi_req_valid_o (debug_req_valid),
      .dmi_req_ready_i (debug_req_ready),
      .dmi_req_o       (debug_req),
      .dmi_resp_valid_i(debug_resp_valid),
      .dmi_resp_ready_o(debug_resp_ready),
      .dmi_resp_i      (debug_resp),
      .tck_i           (tck_i),
      .tms_i           (tms_i),
      .trst_ni         (rst_ni),
      .td_i            (td_i),
      .td_o            (td_o),
      .tdo_oe_o        ()
  );

  dm::hartinfo_t hartinfo;
  assign hartinfo = '{
      zero1      : '0,
      nscratch   : 2,
      zero0      : '0,
      dataaccess : 1'b1,
      datasize   : dm::DataCount,
      dataaddr   : dm::DataAddr
  };

  logic        dm_slave_req;
  logic        dm_slave_we;
  logic [31:0] dm_slave_addr;
  logic [3:0]  dm_slave_be;
  logic [31:0] dm_slave_wdata;
  logic [31:0] dm_slave_rdata;

  logic        dm_master_req;
  logic [31:0] dm_master_add;
  logic        dm_master_we;
  logic [31:0] dm_master_wdata;
  logic [3:0]  dm_master_be;
  logic        dm_master_gnt;
  logic        dm_master_r_valid;
  logic [31:0] dm_master_r_rdata;

  axi2mem #(
      .AXI_ID_WIDTH   (4),
      .AXI_ADDR_WIDTH (32),
      .AXI_DATA_WIDTH (32),
      .AXI_USER_WIDTH (1)
  ) i_dm_axi2mem (
      .clk_i  (clk_i),
      .rst_ni (rst_ni),
      .slave  (master[IDX_DM]),
      .req_o  (dm_slave_req),
      .we_o   (dm_slave_we),
      .addr_o (dm_slave_addr),
      .be_o   (dm_slave_be),
      .data_o (dm_slave_wdata),
      .data_i (dm_slave_rdata)
  );

  dm_top #(
      .NrHarts         (1),
      .BusWidth        (32),
      .SelectableHarts (1'b1)
  ) i_dm_top (
      .clk_i            (clk_i),
      .rst_ni           (rst_ni), // PoR(Power-on Reset)
      .testmode_i       (1'b0),
      .ndmreset_o       (ndmreset),
      .dmactive_o       (),
      .debug_req_o      (debug_req_irq),
      .unavailable_i    ('0),
      .hartinfo_i       (hartinfo),
      .slave_req_i      (dm_slave_req),
      .slave_we_i       (dm_slave_we),
      .slave_addr_i     (dm_slave_addr),
      .slave_be_i       (dm_slave_be),
      .slave_wdata_i    (dm_slave_wdata),
      .slave_rdata_o    (dm_slave_rdata),
      .master_req_o     (dm_master_req),
      .master_add_o     (dm_master_add),
      .master_we_o      (dm_master_we),
      .master_wdata_o   (dm_master_wdata),
      .master_be_o      (dm_master_be),
      .master_gnt_i     (dm_master_gnt),
      .master_r_valid_i (dm_master_r_valid),
      .master_r_rdata_i (dm_master_r_rdata),
      .dmi_rst_ni       (rst_ni),
      .dmi_req_valid_i  (debug_req_valid),
      .dmi_req_ready_o  (debug_req_ready),
      .dmi_req_i        (debug_req),
      .dmi_resp_valid_o (debug_resp_valid),
      .dmi_resp_ready_i (debug_resp_ready),
      .dmi_resp_o       (debug_resp)
  );

  axi_adapter #(
      .ADDR_WIDTH         (32),
      .DATA_WIDTH         (32),
      .AXI_DATA_WIDTH     (32),
      .AXI_ID_WIDTH       (4),
      .MAX_OUTSTANDING_AW (7),
      .axi_req_t          (axi_req_t),
      .axi_rsp_t          (axi_resp_t)
  ) i_axi_adapter_dm (
      .clk_i                 (clk_i),
      .rst_ni                (rst_ni),
      .req_i                 (dm_master_req),
      .type_i                (1'b0),
      .amo_i                 (4'b0000),
      .gnt_o                 (dm_master_gnt),
      .addr_i                (dm_master_add),
      .we_i                  (dm_master_we),
      .wdata_i               (dm_master_wdata),
      .be_i                  (dm_master_be),
      .size_i                (2'b10),
      .id_i                  ('0),
      .valid_o               (dm_master_r_valid),
      .rdata_o               (dm_master_r_rdata),
      .id_o                  (),
      .critical_word_o       (),
      .critical_word_valid_o (),
      .axi_req_o             (dm_axi_m_req),
      .axi_resp_i            (dm_axi_m_resp)
  );

  logic        boot_req;
  logic        boot_we;
  logic [31:0] boot_addr;
  logic [31:0] boot_wdata;
  logic [31:0] boot_rdata;

  bootram i_bootram (
      .clk_i (clk_i),
      .rst_ni(rst_ni),
      .req_i (boot_req),
      .wen_i (boot_we),
      .addr_i(boot_addr[5:2]),
      .data_i(boot_wdata),
      .data_o(boot_rdata)
  );

  axi2mem #(
      .AXI_ID_WIDTH   (4),
      .AXI_ADDR_WIDTH (32),
      .AXI_DATA_WIDTH (32),
      .AXI_USER_WIDTH (1)
  ) i_axi2boot (
      .clk_i  (clk_i),
      .rst_ni (ndmreset_n),
      .slave  (master[IDX_BOOT]),
      .req_o  (boot_req),
      .we_o   (boot_we),
      .addr_o (boot_addr),
      .be_o   (),
      .data_o (boot_wdata),
      .data_i (boot_rdata)
  );

  logic        sram_req;
  logic        sram_we;
  logic [31:0] sram_addr;
  logic [31:0] sram_wdata;
  logic [3:0]  sram_be;
  logic [31:0] sram_rdata;

  my_mainmem #(
      .INIT_FILE(INIT_FILE)
  ) i_mainmem (
      .clk_i  (clk_i),
      .rst_ni (ndmreset_n),
      .req_i  (sram_req),
      .we_i   (sram_we),
      .be_i   (sram_be),
      .addr_i (sram_addr),
      .wdata_i(sram_wdata),
      .rdata_o(sram_rdata)
  );

  axi2mem #(
      .AXI_ID_WIDTH   (4),
      .AXI_ADDR_WIDTH (32),
      .AXI_DATA_WIDTH (32),
      .AXI_USER_WIDTH (1)
  ) i_axi2sram (
      .clk_i  (clk_i),
      .rst_ni (ndmreset_n),
      .slave  (master[IDX_SRAM]),
      .req_o  (sram_req),
      .we_o   (sram_we),
      .addr_o (sram_addr),
      .be_o   (sram_be),
      .data_o (sram_wdata),
      .data_i (sram_rdata)
  );

  // ChipDesign NPU subsystem (Matrix Unit + Vector Unit behind MMIO wrapper)
  logic        npu_req;
  logic        npu_we;
  logic [31:0] npu_addr;
  logic [31:0] npu_wdata;
  logic [31:0] npu_rdata;

  // NPU DMA config signals
  logic [31:0] dma_src;
  logic [31:0] dma_dst;
  logic [31:0] dma_len;
  logic        dma_start;
  logic        dma_irq_en;
  logic        dma_clear_done;
  logic        dma_busy;
  logic        dma_done;

  my_npu_subsystem i_npu_subsystem (
      .clk_i(clk_i), .rst_ni(ndmreset_n), .req_i(npu_req), .we_i(npu_we),
      .addr_i(npu_addr), .wdata_i(npu_wdata), .rdata_o(npu_rdata),
      .irq_o(npu_irq),
      .dma_src_o        (dma_src),
      .dma_dst_o        (dma_dst),
      .dma_len_o        (dma_len),
      .dma_start_o      (dma_start),
      .dma_irq_en_o     (dma_irq_en),
      .dma_clear_done_o (dma_clear_done),
      .dma_busy_i       (dma_busy),
      .dma_done_i       (dma_done)
  );

  axi2mem #(
      .AXI_ID_WIDTH(4), .AXI_ADDR_WIDTH(32), .AXI_DATA_WIDTH(32), .AXI_USER_WIDTH(1)
  ) i_axi2npu (
      .clk_i(clk_i), .rst_ni(ndmreset_n), .slave(master[IDX_NPU]),
      .req_o(npu_req), .we_o(npu_we), .addr_o(npu_addr), .be_o(),
      .data_o(npu_wdata), .data_i(npu_rdata)
  );

  // -------------------------------------------------------------------------
  // NPU DMA engine + AXI adapter
  // -------------------------------------------------------------------------
  logic        dma_m_req;
  logic        dma_m_gnt;
  logic [31:0] dma_m_addr;
  logic        dma_m_we;
  logic [3:0]  dma_m_be;
  logic [31:0] dma_m_wdata;
  logic        dma_m_valid;
  logic [31:0] dma_m_rdata;

  npu_dma i_npu_dma (
      .clk_i          (clk_i),
      .rst_ni         (ndmreset_n),
      .src_i          (dma_src),
      .dst_i          (dma_dst),
      .len_i          (dma_len),
      .start_i        (dma_start),
      .irq_en_i       (dma_irq_en),
      .clear_done_i   (dma_clear_done),
      .busy_o         (dma_busy),
      .done_o         (dma_done),
      .irq_o          (dma_irq),
      .req_o          (dma_m_req),
      .addr_o         (dma_m_addr),
      .we_o           (dma_m_we),
      .be_o           (dma_m_be),
      .wdata_o        (dma_m_wdata),
      .gnt_i          (dma_m_gnt),
      .valid_i        (dma_m_valid),
      .rdata_i        (dma_m_rdata)
  );

  axi_adapter #(
      .ADDR_WIDTH         (32),
      .DATA_WIDTH         (32),
      .AXI_DATA_WIDTH     (32),
      .AXI_ID_WIDTH       (4),
      .MAX_OUTSTANDING_AW (7),
      .axi_req_t          (axi_req_t),
      .axi_rsp_t          (axi_resp_t)
  ) i_axi_adapter_dma (
      .clk_i                 (clk_i),
      .rst_ni                (ndmreset_n),
      .req_i                 (dma_m_req),
      .type_i                (1'b0),
      .amo_i                 (4'b0000),
      .gnt_o                 (dma_m_gnt),
      .addr_i                (dma_m_addr),
      .we_i                  (dma_m_we),
      .wdata_i               (dma_m_wdata),
      .be_i                  (dma_m_be),
      .size_i                (2'b10),
      .id_i                  (4'b0011),
      .valid_o               (dma_m_valid),
      .rdata_o               (dma_m_rdata),
      .id_o                  (),
      .critical_word_o       (),
      .critical_word_valid_o (),
      .axi_req_o             (dma_axi_req),
      .axi_resp_i            (dma_axi_resp)
  );

endmodule
