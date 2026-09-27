`timescale 1ns/1ps
module tb_vector_unit #(
    parameter LANES = 8,
    parameter DATA_WIDTH = 8,
    parameter EXHAUSTIVE = 1
);
    localparam [2:0] VU_ADD = 0, VU_SUB = 1, VU_MAX = 2, VU_MIN = 3, VU_MOV = 4;
    localparam VECTOR_A = 0, VACC = 1, VECTOR_B = 0, SCALAR = 1;
    localparam OUTPUT = 0, DEST_VACC = 1;
    localparam MAX_VALUE = (1 << (DATA_WIDTH-1)) - 1;
    localparam MIN_VALUE = -(1 << (DATA_WIDTH-1));
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    reg in_valid = 0;
    wire in_ready;
    reg [2:0] opcode = VU_MOV;
    reg src_a_sel = VECTOR_A;
    reg src_b_sel = VECTOR_B;
    reg dst_sel = OUTPUT;
    reg [DATA_WIDTH-1:0] scalar = 0;
    reg [LANES-1:0] lane_mask = {LANES{1'b1}};
    reg [LANES*DATA_WIDTH-1:0] vec_a = 0, vec_b = 0;
    wire [LANES*DATA_WIDTH-1:0] vec_out;
    wire out_valid;
    reg out_ready = 1;
    vector_unit #(.LANES(LANES), .DATA_WIDTH(DATA_WIDTH)) dut (.*);

    // Independent integer model uses relational comparison, never the DUT's
    // subtraction-sign or top-bit saturation implementation.
    integer model_vacc [0:LANES-1];
    integer expected [0:LANES-1];
    integer saved_a [0:LANES-1], saved_b [0:LANES-1];
    integer vacc_a [0:LANES-1], vacc_b [0:LANES-1];
    integer saved_op, saved_tx;
    reg pending_output = 0;
    integer accepted = 0, consumed = 0, cancelled = 0;
    integer cycle = 0, stalls = 0, bubbles = 0;
    integer run_length = 0, max_run = 0;
    integer op_count [0:4];
    integer monitor_lane, a_value, b_value, value;
    reg was_stalled;
    reg [LANES*DATA_WIDTH-1:0] held_output;

    task check_output;
        integer l;
        begin
            for (l = 0; l < LANES; l = l + 1)
                if ($signed(vec_out[l*DATA_WIDTH +: DATA_WIDTH]) !== expected[l])
                    $fatal(1, "tx=%0d opcode=%0d lane=%0d A=%0d B/scalar=%0d expected=%0d actual=%0d",
                           saved_tx, saved_op, l, saved_a[l], saved_b[l], expected[l],
                           $signed(vec_out[l*DATA_WIDTH +: DATA_WIDTH]));
        end
    endtask

    // Inspect handshakes before NBA updates, then registered state after NBA.
    // Only one OUTPUT may be pending; VACC writes must never create an output.
    always @(posedge clk) begin
        cycle = cycle + 1;
        if (cycle > 1000000) $fatal(1, "Vector Unit timeout");
        if (rst) begin
            if (pending_output) cancelled = cancelled + 1;
            pending_output = 0;
            for (monitor_lane = 0; monitor_lane < LANES; monitor_lane = monitor_lane + 1)
                model_vacc[monitor_lane] = 0;
            #1;
            if (out_valid !== 1'b0 || in_ready !== 1'b0 || vec_out !== 0 || dut.vacc !== 0)
                $fatal(1, "Reset did not clear output/VACC/ready-valid state");
        end else begin
            if (out_valid !== pending_output)
                $fatal(1, "Unexpected/missing output valid at cycle %0d", cycle);
            was_stalled = out_valid && !out_ready;
            held_output = vec_out;
            if (was_stalled) begin
                stalls = stalls + 1;
                if (in_ready !== 1'b0) $fatal(1, "Input ready during output stall");
            end
            if (!in_valid && in_ready) bubbles = bubbles + 1;
            if (out_valid && out_ready) begin
                check_output();
                pending_output = 0;
                consumed = consumed + 1;
                run_length = run_length + 1;
                if (run_length > max_run) max_run = run_length;
            end else run_length = 0;
            if (in_valid && in_ready) begin
                accepted = accepted + 1;
                if (opcode <= VU_MOV) begin
                    op_count[opcode] = op_count[opcode] + 1;
                    if (dst_sel == OUTPUT) begin
                        if (pending_output) $fatal(1, "Overwriting pending output");
                        pending_output = 1;
                        saved_op = opcode;
                        saved_tx = accepted;
                    end
                    for (monitor_lane = 0; monitor_lane < LANES; monitor_lane = monitor_lane + 1) begin
                        a_value = (src_a_sel == VACC) ? model_vacc[monitor_lane] :
                            $signed(vec_a[monitor_lane*DATA_WIDTH +: DATA_WIDTH]);
                        b_value = (src_b_sel == SCALAR) ? $signed(scalar) :
                            $signed(vec_b[monitor_lane*DATA_WIDTH +: DATA_WIDTH]);
                        case (opcode)
                            VU_ADD: value = a_value + b_value;
                            VU_SUB: value = a_value - b_value;
                            VU_MAX: value = (a_value > b_value) ? a_value : b_value;
                            VU_MIN: value = (a_value < b_value) ? a_value : b_value;
                            VU_MOV: value = a_value;
                            default: value = 0;
                        endcase
                        if (opcode == VU_ADD || opcode == VU_SUB) begin
                            if (value > MAX_VALUE) value = MAX_VALUE;
                            if (value < MIN_VALUE) value = MIN_VALUE;
                        end
                        if (dst_sel == DEST_VACC) begin
                            vacc_a[monitor_lane] = a_value;
                            vacc_b[monitor_lane] = b_value;
                            if (lane_mask[monitor_lane]) model_vacc[monitor_lane] = value;
                        end else begin
                            saved_a[monitor_lane] = a_value;
                            saved_b[monitor_lane] = b_value;
                            expected[monitor_lane] = lane_mask[monitor_lane] ? value : 0;
                        end
                    end
                end
            end
            #1;
            if (out_valid !== pending_output)
                $fatal(1, "Registered latency/valid mismatch at cycle %0d", cycle);
            if (pending_output) check_output();
            if (was_stalled && (out_valid !== 1'b1 || vec_out !== held_output))
                $fatal(1, "Output changed during backpressure");
            for (monitor_lane = 0; monitor_lane < LANES; monitor_lane = monitor_lane + 1)
                if ($signed(dut.vacc[monitor_lane*DATA_WIDTH +: DATA_WIDTH]) !== model_vacc[monitor_lane])
                    $fatal(1, "VACC tx=%0d opcode=%0d lane=%0d A=%0d B/scalar=%0d expected=%0d actual=%0d",
                           accepted, opcode, monitor_lane, vacc_a[monitor_lane], vacc_b[monitor_lane], model_vacc[monitor_lane],
                           $signed(dut.vacc[monitor_lane*DATA_WIDTH +: DATA_WIDTH]));
        end
    end

    task send;
        input [2:0] op;
        input sa, sb, dst;
        input [LANES*DATA_WIDTH-1:0] va, vb;
        input [DATA_WIDTH-1:0] sc;
        input [LANES-1:0] mask;
        begin
            @(negedge clk);
            in_valid = 1;
            opcode = op; src_a_sel = sa; src_b_sel = sb; dst_sel = dst;
            vec_a = va; vec_b = vb; scalar = sc; lane_mask = mask;
            @(posedge clk);
            while (!in_ready) @(posedge clk);
        end
    endtask

    task drain;
        begin
            @(negedge clk);
            in_valid = 0;
            out_ready = 1;
            @(posedge clk);
            @(negedge clk);
            if (out_valid || pending_output) $fatal(1, "Drain left pending output");
        end
    endtask

    reg [LANES*DATA_WIDTH-1:0] a, b;
    reg [LANES-1:0] mask;
    integer i, j, k, op, pair_index;
    integer pool_window [0:3][0:LANES-1];
    integer pool_expected [0:LANES-1];

    task pooling;
        input [2:0] pool_op;
        integer position, l, x;
        begin
            for (position = 0; position < 4; position = position + 1) begin
                for (l = 0; l < LANES; l = l + 1) begin
                    x = ((position*71 + l*37) % 256) - 128;
                    pool_window[position][l] = x;
                    a[l*DATA_WIDTH +: DATA_WIDTH] = x;
                end
                if (position == 0)
                    send(VU_MOV, VECTOR_A, VECTOR_B, DEST_VACC, a, 0, 0, {LANES{1'b1}});
                else
                    send(pool_op, VACC, VECTOR_B, DEST_VACC, 0, a, 0, {LANES{1'b1}});
            end
            for (l = 0; l < LANES; l = l + 1) begin
                pool_expected[l] = pool_window[0][l];
                for (position = 1; position < 4; position = position + 1) begin
                    x = pool_window[position][l];
                    if ((pool_op == VU_MAX && x > pool_expected[l]) ||
                        (pool_op == VU_MIN && x < pool_expected[l])) pool_expected[l] = x;
                end
            end
            send(VU_MOV, VACC, VECTOR_B, OUTPUT, 0, 0, 0, {LANES{1'b1}});
            #2;
            for (l = 0; l < LANES; l = l + 1)
                if ($signed(vec_out[l*DATA_WIDTH +: DATA_WIDTH]) !== pool_expected[l])
                    $fatal(1, "2x2 pool opcode=%0d lane=%0d expected=%0d actual=%0d",
                           pool_op, l, pool_expected[l], $signed(vec_out[l*DATA_WIDTH +: DATA_WIDTH]));
            drain();
        end
    endtask

    task random_stream;
        input integer count;
        input integer random_stalls;
        integer sent, l;
        reg pending;
        begin
            sent = 0; pending = 0;
            while (sent < count) begin
                @(negedge clk);
                out_ready = !random_stalls || ($urandom_range(0, 3) != 0);
                if (!pending) begin
                    in_valid = !random_stalls || ($urandom_range(0, 3) != 0);
                    if (in_valid) begin
                        opcode = $urandom_range(0, 7);
                        src_a_sel = $urandom; src_b_sel = $urandom;
                        dst_sel = random_stalls ? ($urandom_range(0, 1)) : OUTPUT;
                        scalar = $urandom;
                        for (l = 0; l < LANES; l = l + 1) begin
                            vec_a[l*DATA_WIDTH +: DATA_WIDTH] = $urandom;
                            vec_b[l*DATA_WIDTH +: DATA_WIDTH] = $urandom;
                            lane_mask[l] = $urandom;
                        end
                        pending = 1;
                    end
                end
                @(posedge clk);
                if (in_valid && in_ready) begin sent = sent + 1; pending = 0; end
            end
            drain();
        end
    endtask

    initial begin
`ifdef DUMP_VCD
        $dumpfile("vector_unit.vcd");
        $dumpvars(0, tb_vector_unit);
`endif
        if (DATA_WIDTH != 8 || LANES < 1) $fatal(1, "This regression requires INT8 and LANES >= 1");
        for (i = 0; i < 5; i = i + 1) op_count[i] = 0;
        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0;
        // All eight directed cases also run when LANES is smaller than eight.
        for (k = 0; k < 8; k = k + 1) begin
            for (i = 0; i < LANES; i = i + 1) begin
                case ((i+k) % 8)
                    0: begin a[i*DATA_WIDTH +: DATA_WIDTH] = 1; b[i*DATA_WIDTH +: DATA_WIDTH] = 2; end
                    1: begin a[i*DATA_WIDTH +: DATA_WIDTH] = -5; b[i*DATA_WIDTH +: DATA_WIDTH] = 3; end
                    2: begin a[i*DATA_WIDTH +: DATA_WIDTH] = 127; b[i*DATA_WIDTH +: DATA_WIDTH] = 1; end
                    3: begin a[i*DATA_WIDTH +: DATA_WIDTH] = 100; b[i*DATA_WIDTH +: DATA_WIDTH] = 50; end
                    4: begin a[i*DATA_WIDTH +: DATA_WIDTH] = -128; b[i*DATA_WIDTH +: DATA_WIDTH] = -1; end
                    5: begin a[i*DATA_WIDTH +: DATA_WIDTH] = -100; b[i*DATA_WIDTH +: DATA_WIDTH] = -50; end
                    6: begin a[i*DATA_WIDTH +: DATA_WIDTH] = 127; b[i*DATA_WIDTH +: DATA_WIDTH] = -128; end
                    7: begin a[i*DATA_WIDTH +: DATA_WIDTH] = -128; b[i*DATA_WIDTH +: DATA_WIDTH] = 127; end
                endcase
            end
            for (op = 0; op < 5; op = op + 1)
                send(op, VECTOR_A, VECTOR_B, OUTPUT, a, b, 0, {LANES{1'b1}});
        end
        drain();
        $display("PASS directed ADD/SUB saturation, MAX/MIN signed corners, MOV");

        for (i = 0; i < LANES; i = i + 1) begin
            case (i % 8)
                0: j = -10; 1: j = 0; 2: j = 3; 3: j = 5;
                4: j = 6; 5: j = 10; 6: j = -128; 7: j = 127;
            endcase
            a[i*DATA_WIDTH +: DATA_WIDTH] = j;
        end
        for (op = 0; op < 5; op = op + 1) begin
            send(op, VECTOR_A, SCALAR, OUTPUT, a, 0, 5, {LANES{1'b1}});
            send(op, VECTOR_A, SCALAR, OUTPUT, a, 0, -7, {LANES{1'b1}});
        end
        // MAX(x, nonzero zero_point) implements asymmetric-quantized ReLU.
        send(VU_MAX, VECTOR_A, SCALAR, DEST_VACC, a, 0, -7, {LANES{1'b1}});
        send(VU_MIN, VACC, SCALAR, DEST_VACC, 0, 0, 10, {LANES{1'b1}});
        send(VU_MOV, VACC, VECTOR_B, OUTPUT, 0, 0, 0, {LANES{1'b1}});
        drain();
        $display("PASS scalar broadcast, nonzero zero_point ReLU, two-operation clamp");

        pooling(VU_MAX);
        pooling(VU_MIN);
        $display("PASS consecutive VACC dependencies and per-channel 2x2 MaxPool/MinPool");

        send(VU_MOV, VECTOR_A, VECTOR_B, DEST_VACC, a, 0, 0, {LANES{1'b1}});
        for (i = 0; i < LANES; i = i + 1) mask[i] = (i % 2 == 0);
        for (op = 0; op < 5; op = op + 1) begin
            send(op, VACC, SCALAR, DEST_VACC, 0, 0, 27, mask);
            send(VU_MOV, VACC, VECTOR_B, OUTPUT, 0, 0, 0, {LANES{1'b1}});
            send(op, VECTOR_A, SCALAR, OUTPUT, a, 0, -13, mask);
            send(op, VACC, SCALAR, DEST_VACC, 0, 0, -128, 0);
            send(op, VECTOR_A, VECTOR_B, OUTPUT, a, b, 0, 0);
        end
        drain();
        // Reserved opcodes must produce no output and must not modify VACC.
        for (op = 5; op < 8; op = op + 1) begin
            send(op, VECTOR_A, VECTOR_B, DEST_VACC, a, b, 0, {LANES{1'b1}});
            send(op, VECTOR_A, VECTOR_B, OUTPUT, a, b, 0, {LANES{1'b1}});
        end
        drain();
        $display("PASS partial/zero masks, preserved VACC lanes, reserved opcodes");

        // Fill the output slot, then hold a dependent VACC request for five
        // blocked edges. It may commit only when the old output is consumed.
        send(VU_MOV, VECTOR_A, VECTOR_B, OUTPUT, a, 0, 0, {LANES{1'b1}});
        @(negedge clk);
        out_ready = 0; in_valid = 1;
        opcode = VU_ADD; src_a_sel = VACC; src_b_sel = SCALAR;
        dst_sel = DEST_VACC; scalar = 1;
        repeat (5) @(posedge clk);
        @(negedge clk); out_ready = 1;
        @(posedge clk);
        send(VU_MOV, VACC, VECTOR_B, OUTPUT, 0, 0, 0, {LANES{1'b1}});
        drain();
        repeat (4) @(posedge clk);
        $display("PASS forced input stall and stable output/VACC under backpressure");

        // Continuous output requests prove one accepted/output vector per edge.
        for (k = 0; k < 48; k = k + 1)
            send(VU_ADD, VECTOR_A, SCALAR, OUTPUT, a, 0, k, {LANES{1'b1}});
        drain();
        if (max_run < 48) $fatal(1, "Throughput only %0d consecutive outputs", max_run);
        random_stream(2000, 1);
        if (stalls < 5 || bubbles == 0) $fatal(1, "Missing stall/bubble coverage");
        for (op = 0; op < 5; op = op + 1)
            if (op_count[op] == 0) $fatal(1, "Missing opcode %0d", op);
        $display("PASS 2000 randomized controls/data/masks with input bubbles/backpressure");

        // Exhaustively cover every signed INT8 operand pair for each opcode.
        // Distribute pairs over packed lanes to keep the regression small.
        if (EXHAUSTIVE) begin
            for (op = 0; op < 5; op = op + 1)
                for (k = 0; k < 65536; k = k + LANES) begin
                    for (i = 0; i < LANES; i = i + 1) begin
                        pair_index = (k + i) % 65536;
                        a[i*DATA_WIDTH +: DATA_WIDTH] = pair_index / 256 - 128;
                        b[i*DATA_WIDTH +: DATA_WIDTH] = pair_index % 256 - 128;
                    end
                    send(op, VECTOR_A, VECTOR_B, OUTPUT, a, b, 0, {LANES{1'b1}});
                end
            drain();
            $display("PASS exhaustive 65536 INT8 operand pairs x 5 opcodes");
        end

        // Synchronous reset during idle, then while an output is blocked.
        @(negedge clk); rst = 1;
        repeat (2) @(posedge clk);
        @(negedge clk); rst = 0;
        send(VU_MOV, VACC, VECTOR_B, OUTPUT, 0, 0, 0, {LANES{1'b1}});
        drain();
        send(VU_MOV, VECTOR_A, VECTOR_B, DEST_VACC, a, 0, 0, {LANES{1'b1}});
        send(VU_MOV, VECTOR_A, VECTOR_B, OUTPUT, a, 0, 0, {LANES{1'b1}});
        @(negedge clk); out_ready = 0; rst = 1;
        repeat (2) @(posedge clk);
        @(negedge clk); rst = 0; in_valid = 0; out_ready = 1;
        send(VU_MOV, VACC, VECTOR_B, OUTPUT, 0, 0, 0, {LANES{1'b1}});
        drain();
        $display("PASS reset during idle/blocked output and clean restart");
        $display("ALL VECTOR UNIT TESTS PASSED LANES=%0d DATA_WIDTH=%0d accepted=%0d outputs=%0d cancelled=%0d stalls=%0d bubbles=%0d max_run=%0d",
                 LANES, DATA_WIDTH, accepted, consumed, cancelled, stalls, bubbles, max_run);
        $finish;
    end
endmodule
