`timescale 1ns/1ps

module kws_workload_tb;
  logic clk_i = 0;
  logic rst_ni = 0;
  wire td_o;
  string firmware;
  int unsigned timeout_cycles = 5_000_000;
  int unsigned cycle_count, base_word, count;
  localparam int MAGIC_WORD = 'h7f8;
  my_soc_top #(.INIT_FILE("")) dut (
    .clk_i(clk_i), .rst_ni(rst_ni),
    .tck_i(1'b0), .tms_i(1'b0), .td_i(1'b0), .td_o(td_o)
  );
  always #10 clk_i = ~clk_i; // 50 MHz

  function automatic logic [31:0] mem(input int unsigned word_index);
    return dut.i_mainmem.i_mainmem.memory[word_index];
  endfunction

  initial begin
    if (!$value$plusargs("firmware=%s", firmware))
      $fatal(1, "FAIL: +firmware=<hex> is required");
    if ($value$plusargs("timeout_cycles=%d", timeout_cycles)) begin end
    $readmemh(firmware, dut.i_mainmem.i_mainmem.memory);
    if (mem(MAGIC_WORD) !== 32'b0)
      $fatal(1, "FAIL: image must initialize magic to zero");
    repeat (8) @(negedge clk_i);
    rst_ni = 1;
    for (cycle_count = 0; cycle_count < timeout_cycles; cycle_count++) begin
      @(negedge clk_i);
      if (mem(MAGIC_WORD) === 32'hdeadbeef)
        $fatal(1, "FAIL: firmware error, results=%08h npu_error=%0d", mem(MAGIC_WORD+1), mem(MAGIC_WORD+3));
      if (mem(MAGIC_WORD) === 32'hc0dec0de) begin
        if ($isunknown(mem(MAGIC_WORD+1)) || mem(MAGIC_WORD+1) < 'h80000000 ||
            mem(MAGIC_WORD+1) >= 'h80001ee0 || (mem(MAGIC_WORD+1) & 3) != 0)
          $fatal(1, "FAIL: invalid result pointer");
        base_word = (mem(MAGIC_WORD+1) - 'h80000000) / 4;
        count = mem(base_word);
        if ($isunknown(count) || count < 2 || count > 21 ||
            base_word + 4 + count*10 > 'h7b8 || mem(base_word+2) !== 0 || mem(MAGIC_WORD+2) !== 1)
          $fatal(1, "FAIL: invalid result header");
        $display("KWS_HEADER schema=1 count=%0d load=%0d error=%0d stack=%0d results=%08h",
            count, mem(base_word+1), mem(base_word+2), mem(base_word+3), mem(MAGIC_WORD+1));
        for (int i = 0; i < count; i++) begin
          $display("KWS_SAMPLE index=%0d raw=%08h,%08h,%08h,%08h prediction=%0d input=%0d compute=%0d read=%0d perf=%0d total=%0d",
              i, mem(base_word+4+i*10), mem(base_word+5+i*10), mem(base_word+6+i*10), mem(base_word+7+i*10),
              mem(base_word+8+i*10), mem(base_word+9+i*10), mem(base_word+10+i*10),
              mem(base_word+11+i*10), mem(base_word+12+i*10), mem(base_word+13+i*10));
        end
        $display("KWS_PASS count=%0d", count);
        $finish;
      end
    end
    $fatal(1, "TIMEOUT: workload after %0d cycles", timeout_cycles);
  end
endmodule
