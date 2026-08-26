; FMan v210 enhanced external-hash lookup, hand-annotated
;
; Documented names used below:
;   IC + 0x00  FD       frame descriptor (16 bytes)
;   IC + 0x10  ICAD     internal-context action descriptor (8 bytes)
;   IC + 0x18  CCBASE   custom-classifier descriptor address
;   IC + 0x1c  KS       KeyGen key size
;   IC + 0x20  PR       parser result (32 bytes)
;   IC + 0x48  HASH     KeyGen 64-bit hash
;   IC + 0x50  KEY      KeyGen key (up to 56 bytes)
;
; Those IC offsets are from LS1046ADPAARM Rev. 0, Table 5-19. Descriptor,
; bucket, regular-record, cumulative-record, flag, and miss-action names follow
; the public FMan enhanced-external-hash host ABI preserved in the ASK patch.
; IC offsets 0x90 and above are firmware-private workspace fields and are named
; by observed use, not by the public IC table.
;
; Register aliases emitted by the focused disassembler:
;   r26/*IC*/     current frame internal-context base
;   r28/*FRAME*/  current frame window/address context
;   r31/*COND*/   condition/special-state register
;
; Equivalent C. The helper calls stand for fixed FMan operations and the few
; firmware-private state conversions whose exact representation is not public.
;
; static void external_hash_lookup(struct fman_task *task)
; {
;     struct fman_ic *ic = task->ic;
;     const struct en_exthash_node *descriptor = muram_pointer(ic->ccbase);
;     uint8_t record_storage[MAX_EN_EHASH_EXT_ENTRY_SIZE];
;     struct en_ehash_entry *record = (void *)record_storage;
;     uint64_t record_address;
;     uint32_t fd_status = load_be32(&ic->fd.command_status);
;     uint16_t pr_word = load_be16(&ic->pr[4]);
;
;     if (bit_is_set(fd_status, 5)) {
;         handle_external_fd_status(task);
;         return;
;     }
;     if ((fd_status & 0x08u) != 0) {
;         handle_external_fd_command(task);
;         return;
;     }
;     prepare_frame_state(task);
;     if ((pr_word & 0x0200u) != 0) {
;         redispatch_controller(task, PRE_BMI_DISCARD_FRAME);
;         return;
;     }
;     if ((pr_word & 0x0044u) != 0) {
;         handle_default_classifier_result(task, descriptor);
;         return;
;     }
;
;     uint32_t hash_bits = descriptor->hash_mask_bits & 0x0fu;
;     uint32_t bucket_mask = (1u << hash_bits) - 1u;
;     const uint8_t *hash_position =
;         &ic->hash[descriptor->hash_bytes_offset];
;     uint32_t bucket_index = load_be16(hash_position) & bucket_mask;
;     uint64_t bucket_address =
;         exthash_table_base(descriptor) +
;         bucket_index * sizeof(struct en_exthash_bucket);
;
;     record_address = dma_read_bucket_head(bucket_address);
;     if (record_address == 0)
;         goto miss;
;
;     for (;;) {
;         dma_read_exact(record_address, record_storage,
;                        MAX_EN_EHASH_ENTRY_SIZE);
;
;         if (record_uses_alternate_link(record_storage)) {
;             record_address = load_be48(&record_storage[0x1e]);
;             if (record_address == 0)
;                 goto miss;
;             continue;
;         }
;
;         if (!record_is_cumulative(record_storage)) {
;             uint16_t flags = load_be16(&record->flags);
;
;             if (flag_is_set(flags, EN_EHASH_ENTRY_INVALID))
;                 goto miss;
;             if (!keys_equal(ic->key, &record_storage[8], ic->ks))
;                 goto miss;
;             break;
;         }
;
;         struct en_cumulative_entry *node = (void *)record_storage;
;         unsigned int match_index;
;         bool matched;
;
;         if (flag_is_set(node->flags, EN_INVALID_CUMULATIVE_NODE))
;             goto next_cumulative_node;
;         matched = find_packed_key(ic->key, ic->ks, node->data,
;                                   node->num_key_entries, &match_index);
;         if (!matched)
;             goto next_cumulative_node;
;         if (flag_is_set(node->flags, EN_INVALID_CUMULATIVE_NODE))
;             goto next_cumulative_node;
;
;         record_address = load_be64(record_storage + node->tbl_entry_index +
;                                    match_index * sizeof(uint64_t));
;         dma_read_exact(record_address, record_storage,
;                        MAX_EN_EHASH_ENTRY_SIZE);
;         if (flag_is_set(load_be16(&record->flags), EN_EHASH_ENTRY_INVALID))
;             goto miss;
;         break;
;
; next_cumulative_node:
;         if (!flag_is_set(node->flags, EN_NEXT_CUMULATIVE_NODE))
;             goto miss;
;         record_address = load_be64(&node->next_entry_addr);
;     }
;
;     uint16_t flags = load_be16(&record->flags);
;     bool update_stats = flag_is_set(flags, EN_EHASH_STATS_ENABLE);
;     bool update_timestamp = flag_is_set(flags, EN_EHASH_TIMESTAMP_ENABLE);
;
;     if (update_stats || update_timestamp) {
;         struct en_ehash_stats_extension stats;
;         struct dma_buffer_result stats_result;
;
;         do {
;             stats_result = acquire_stats_buffer(record_address + 0x100u);
;         } while (stats_result_is_pending(stats_result));
;         stats = *stats_result.buffer;
;
;         if (update_timestamp)
;             stats.timestamp = read_muram_counter(stats.timestamp_counter);
;         if (update_stats) {
;             uint64_t packet_bytes;
;             uint64_t packet_count;
;             bool acquired;
;
;             do {
;                 acquired = load_acquire_u64(&stats.packet_bytes,
;                                             &packet_bytes);
;             } while (!acquired);
;             packet_count = load_be64(&stats.packet_count) + 1;
;             store_be64(&stats.packet_count, packet_count);
;             packet_bytes += fd_frame_length(&ic->fd);
;             store_release_u64(&stats.packet_bytes, packet_bytes);
;         }
;         dma_write_stats(record_address + 0x100u, &stats);
;     }
;
;     size_t parameter_offset = (flags & 0x003fu) << 2;
;     size_t opcode_offset = ((flags >> 6) & 0x001fu) << 2;
;     execute_fe_actions(task, record_storage + opcode_offset,
;                        record_storage + parameter_offset);
;     return;
;
; miss:
;     if (resume_parser_after_special_miss(task))
;         return;
;     if (publish_private_classifier_result(task))
;         return;
;
;     switch (descriptor->miss_action_type) {
;     case EN_EHASH_MISS_ACTION_DONE:
;         redispatch_controller(task, PRE_BMI_PREPARE_TO_ENQUEUE);
;         return;
;     case EN_EHASH_MISS_ACTION_NIA:
;         redispatch_nia(task, descriptor->nia);
;         return;
;     case EN_EHASH_MISS_ACTION_ENQUE:
;         set_task_fqid(task, descriptor->fqid);
;         redispatch_controller(task, PRE_BMI_PREPARE_TO_ENQUEUE);
;         return;
;     case EN_EHASH_MISS_ACTION_DROP:
;         redispatch_controller(task, PRE_BMI_DISCARD_FRAME);
;         return;
;     }
;     /* jmptbl4 performs the switch; each selected xfer14 is a direct stub. */
; }

; ==========================================================================
; FMan Controller action 0x06 entry stub (w6..w7)
;
; Pseudocode:
;   goto custom_classifier_entry;
; ==========================================================================
.org 0x6

controller_action_06_custom_classifier:
    xfer14 custom_classifier_entry          ; dispatch Controller action 0x06
    nop                                        ; fill slot

; ==========================================================================
; Enhanced external-hash lookup (w1584..w1765)
; ==========================================================================
.org 0x630

; Validate the incoming frame state before interpreting CCBASE as an enhanced
; external-hash descriptor.
;
; Pseudocode:
;   descriptor = muram_pointer(ic.ccbase);
;   fd_status = load_be32(&ic.fd.command_status);
;   if (bit_is_set(fd_status, 5)) goto external_status_path;
;   if ((fd_status & 0x08) != 0) goto external_dispatch_11918;
;   prepare_frame_state();
;   pr_word = load_be16(&ic.pr[4]);
;   if ((pr_word & 0x0200) != 0) goto alternate_result;
;   if ((pr_word & 0x0044) != 0) goto default_result;
;   goto ehash_load_descriptor;
custom_classifier_entry:
    memw.read r0, r26/*IC*/, 0x18              ; descriptor = IC.CCBASE
    memw.read r3, r26/*IC*/, 0xc               ; fd_status = IC.FD[12..15]
    brbitset16 r3, 5, w1847                    ; exceptional FD state leaves this extract
    tstandi16 r3, 0x8                          ; test another FD command/status condition
    cbrnz16 w11918                             ; route that condition to its shared handler
    state 32, r28/*FRAME*/, 4, 3               ; bind current-frame state used below
    memh.read r18, r26/*IC*/, 0x24             ; pr_word = halfword(IC.PR + 4)
    tstandi16 r18, 0x200                       ; test PR special-result bit 0x0200
    cbrnz16 ehash_alternate_result             ; special result uses alternate/drop path
    memh.read r18, r26/*IC*/, 0x24             ; reload the parser-result halfword
    tstandi16 r18, 0x44                        ; test PR default-result conditions
    cbrnz16 ehash_default_classifier_result    ; bypass lookup when either bit is set

; Read the first eight descriptor bytes. In big-endian microcode order they
; supply control fields and the 40-bit external bucket-table address.
;
; Pseudocode:
;   descriptor_head = load_be64(descriptor);
;   hash_bits = descriptor->hash_mask_bits & 0x0f;
;   bucket_mask = (1 << hash_bits) - 1;
;   hash_ptr = &ic.hash[descriptor->hash_bytes_offset];
;   bucket_index = load_be16(hash_ptr) & bucket_mask;
;   bucket_addr = exthash_table_base(descriptor) + bucket_index * 16;
ehash_load_descriptor:
    memd.read r18, r0, 0x0                     ; r18:r19 = descriptor bytes 0..7
ehash_form_bucket_index:
    memb.read r20, r0, 0xb                     ; hash_bits = descriptor.hash_mask_bits
    andi16 r20, 0xf                            ; hash_bits &= 15
    li16 r14, 0x1                              ; mask_seed = 1
    lsl32 r14, r20, r20                       ; mask_limit = 1 << hash_bits
    subi16 r20, 0x1                            ; bucket_mask = mask_limit - 1
    addlane8 r26/*IC*/, r11, 3, 0x48           ; hash_ptr = IC + offsetof(HASH)
    bitfield.ins3 r18, r11                     ; hash_ptr += descriptor.hash_bytes_offset
    memh.read r11, r11, 0x0                    ; hash_slice = load16(hash_ptr)
    and32 r11, r20, r11                        ; bucket_index = hash_slice & bucket_mask
    lsl32i r11, 0x4, r11                       ; bucket_offset = bucket_index * 16
    add32 r19, r11, r19                        ; table_base.low += bucket_offset

; Finish the 40-bit addition and expand the descriptor's compressed internal
; buffer-pool address into the MURAM workspace used for record DMA.
;
; Pseudocode:
;   bucket_addr = add_u40(exthash_table_base(descriptor), bucket_offset);
;   workspace = expand_muram_address(descriptor->int_buf_pool_addr);
;   ic.private_record_workspace = workspace;
ehash_finish_bucket_address:
    adci16 r18, 0x0                            ; bucket_addr.high += carry from low-word addition
    state 32, r4, 0, 1                         ; obtain the MURAM address prefix/context
    andi16 r4, 0xff                            ; retain the prefix byte
ehash_prepare_record_workspace:
    memh.read r20, r0, 0x8                     ; compressed = descriptor.int_buf_pool_addr
    lsl32i r20, 0x8, r20                       ; position descriptor address bits
    lsl32i r4, 0x8, r4                         ; position the context prefix
    add32 r20, r4, r20                         ; workspace = compressed + prefix
    memw.write r20, r26/*IC*/, 0x98            ; IC.private_record_workspace = workspace
    immhi16 r27, 0x2000                        ; select external-memory DMA mode
    addlane8 r26/*IC*/, r8, 3, 0x90            ; bucket_head_slot = IC.private + 0x90

; Fetch the eight-byte bucket head. `struct en_exthash_bucket` is 16 bytes and
; its first member `h` is the 64-bit external address of the first record.
;
; Pseudocode:
;   dma_read8(bucket_addr, &ic.private_bucket_head);
;   record_addr = ic.private_bucket_head;
;   if (record_addr == 0) goto ehash_miss;
ehash_fetch_bucket_head:
    dma.read8 r18, r8                          ; DMA bucket.h into bucket_head_slot
    wait.cont ehash_check_bucket_head           ; suspend until the bucket-head DMA completes
    nop                                        ; fill slot
ehash_check_bucket_head:
    memd.read r18, r26/*IC*/, 0x90             ; record_addr = IC.private_bucket_head
    testor32 r18, r19, 18                      ; condition = record_addr.high | low
    cbrz16.comp ehash_miss                     ; an empty bucket is a miss

; Fetch one complete regular or cumulative record. Both public layouts occupy
; 256 bytes and are 256-byte aligned.
;
; Pseudocode:
;   dma_read256(record_addr, ic.private_record_workspace);
;   complete_record_dma();
;   goto ehash_classify_record;
ehash_fetch_record:
    immhi16 r27, 0x2000                        ; select 256-byte external DMA mode
    memw.read r8, r26/*IC*/, 0x98              ; destination = record workspace
    dma.read256 r18, r8                        ; fetch record at r18:r19
    memb.read r10, r26/*IC*/, 0x14             ; read ICAD byte used by completion state
    state 31, r10, r10, 4, 7                   ; fold completion/context into task state
    wait.cont ehash_classify_record             ; suspend until the record DMA completes
    nop                                        ; fill slot

; The first byte is overloaded by the two public record layouts. Bit 7 is
; INVALID in a regular `en_ehash_entry`, and CUMULATIVE in an
; `en_cumulative_entry`; the paired tests route using the active interpretation.
;
; Pseudocode:
;   ic.private_compare_status = 0;
;   record = ic.private_record_workspace;
;   flags_hi = record[0];
;   if (is_cumulative(flags_hi)) goto ehash_compare_cumulative_node;
;   if (is_invalid_regular(flags_hi)) goto ehash_miss;
;   goto ehash_compare_regular_key;
ehash_classify_record:
    li16 r14, 0x0                              ; zero = 0
    memb.write r14, r26/*IC*/, 0x9c            ; clear private compare/status byte
    memw.read r8, r26/*IC*/, 0x98              ; record = record workspace
    memb.read r9, r8, 0x0                      ; flags_hi = record[0]
    brbitset16 r9, 7, ehash_compare_cumulative_node ; cumulative representation path
    memb.read r9, r8, 0x0                      ; reload for regular interpretation
    brbitset16 r9, 7, ehash_miss               ; invalid regular record is a miss

; Compare the KeyGen key against the regular record key at offset 8. The
; bitfield/state sequence configures and runs a variable-length compare; status
; bit 0x10 in r0 is the mismatch result consumed here.
;
; Pseudocode:
;   key = ic.key;
;   key_last = ic.ks - 1;
;   record_key = record + 8;
;   compare_state = compare_bytes(key, record_key, key_last + 1);
;   if (bit_is_set(compare_state, 4)) goto ehash_miss;
;   goto ehash_acquire_record_stats;
ehash_compare_regular_key:
    addlane8 r26/*IC*/, r16, 3, 0x50           ; key = &IC.KEY[0]
    memb.read r17, r26/*IC*/, 0x1c             ; key_size = IC.KS
    subi16 r17, 0x1                            ; key_last = key_size - 1
    bitfield 0, r17, r16, 24, 48               ; configure key source and final byte
    addlane8 r8, r18, 3, 0x8                   ; record_key = record + 8
    li16 r17, 0x1                              ; one comparison span
    bitfield 0, r17, r18, 24, 48               ; configure record-key source
    state 31, r31/*COND*/, r31/*COND*/, 16, 32 ; arm hidden compare state
    keycmp.run                                  ; r0 = 0 when equal, 0x10 when different
    xfer14 w1649                            ; join after compare completion
    nop                                        ; fill slot
    andi16z 0x10                               ; mismatch = r0 & 0x10; publish Z from mismatch
    cbrnz16 ehash_miss                         ; key mismatch is a miss
    memw.read r8, r26/*IC*/, 0x98              ; restore record workspace pointer
    xfer14 ehash_acquire_record_stats       ; direct low-14 transfer to word 1671
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding
    nop                                        ; reserved dispatch padding

; These four aligned B7 entry stubs have no decoded inbound references. Their
; destinations are meaningful record-processing continuations, but the
; mechanism that enters the stubs has not been found. They are not selected by
; the B7 at word 1652: LS1046A one-hot tests over four r9 flag values and three
; increasingly native prefixes always reached that instruction's encoded word
; 1671 target. Canonical `xfer14 label` syntax has no register, selector, or raw
; high-bit operand; the assembler derives bits 15..14 from displacement sign.
;
; Pseudocode:
;   unresolved_entry_0: goto ehash_enter_record_actions;
;   unresolved_entry_1: goto ehash_update_record_counters;
;   unresolved_entry_2: goto ehash_update_record_timestamp;
;   unresolved_entry_3: goto ehash_acquire_record_stats;
ehash_unresolved_entry_stubs:
    xfer14 ehash_enter_record_actions       ; unresolved external entry stub
    nop                                        ; table alignment/fill
    xfer14 ehash_update_record_counters     ; unresolved external entry stub
    nop                                        ; table alignment/fill
    xfer14 ehash_update_record_timestamp    ; unresolved external entry stub
    nop                                        ; table alignment/fill
    xfer14 ehash_acquire_record_stats       ; unresolved external entry stub

; The extended entry starts at external record address + 256. The host layout
; names packet_count, packet_bytes, timestamp, and timestamp_counter there.
;
; Pseudocode:
;   stats_ext_addr = record_addr + 0x100;
;   do {
;     stats_result = acquire_stats_buffer64(stats_ext_addr, workspace);
;   } while (stats_result_is_pending(stats_result));
;   stats_buffer = stats_result_buffer(stats_result);
;   stats_buffer.timestamp =
;       read_muram_counter(stats_buffer.timestamp_counter);
ehash_acquire_record_stats:
    immhi16 r27, 0x2000                        ; select external-memory DMA mode
    memd.read r10, r26/*IC*/, 0x90             ; stats_ext_addr = external record address
    addi16 r11, 0x100                          ; stats_ext_addr.low += 256
    adci16 r10, 0x0                            ; stats_ext_addr.high += carry from low word
    pipeline.setup 25, 31, 31, 20, 0           ; configure status-aware DMA bookkeeping
    memw.read r8, r26/*IC*/, 0x98              ; statistics destination/workspace
    dma.status64 r10, r8                       ; publish status/result word at workspace+8
    wait.cont w1680                            ; suspend until the operation completes
    nop                                        ; fill slot
    memw.read r8, r26/*IC*/, 0x98              ; recover record workspace
    memw.read r8, r8, 0x8                      ; status/result = workspace[8]
    tstandhi16 r8, 0xc000                      ; test high DMA-pending bits
    cbrnz16 w1672                              ; reissue while status says pending
    ; A non-pending result is the statistics-buffer pointer.
    memw.read r13, r8, 0x18                    ; counter_ptr = stats_buffer.timestamp_counter
    memw.read r13, r13, 0x0                    ; now = *counter_ptr
    memw.write r13, r8, 0x10                   ; stats_buffer.timestamp = now

; Update the two documented 64-bit counters under an FMan semaphore memory
; operation. The frame length comes from the FD word at IC+8. The complete
; 64-byte aligned statistics extension is then written back to DDR.
;
; Pseudocode:
;   do {
;     acquired = load_acquire_u64(&stats_buffer.packet_bytes,
;                                 &packet_bytes);
;   } while (!acquired);
;   frame_length = fd_frame_length(&ic.fd);
;   packet_count = load_be64(&stats_buffer.packet_count) + 1;
;   store_be64(&stats_buffer.packet_count, packet_count);
;   packet_bytes += frame_length;
;   store_release_u64(&stats_buffer.packet_bytes, packet_bytes);
;   dma_write64(stats_ext_addr, &stats_buffer);
ehash_update_record_counters:
    ld.sm 3, r10, r8, 0x8                     ; acquire and load packet_bytes
    retry.sm ehash_update_record_counters      ; retry until acquisition succeeds
    memw.read r12, r26/*IC*/, 0x8              ; fd_length_word = IC.FD[8..11]
    condimm 10, r12, 65520                     ; frame_length = extract FD length bits
    memd.read r16, r8, 0x0                     ; packet_count = load64(stats_buffer)
    addi16 r17, 0x1                            ; packet_count.low += 1
    adci16 r16, 0x0                            ; packet_count.high += carry from low word
    memd.write r16, r8, 0x0                    ; store64(packet_count)
    add32 r11, r12, r11                        ; packet_bytes.low += frame_length
    adci16 r10, 0x0                            ; packet_bytes.high += carry from low word
    st.sm 3, r10, r8, 0x8                     ; store packet_bytes and release
ehash_writeback_record_stats:
    memd.read r10, r26/*IC*/, 0x90             ; stats_ext_addr = record_addr
    addi16 r11, 0x100                          ; stats_ext_addr.low += 256
    adci16 r10, 0x0                            ; stats_ext_addr.high += carry from low word
    immhi16 r27, 0x2000                        ; select external-memory DMA mode
    pipeline.setup 25, 31, 31, 20, 0           ; configure statistics writeback
    dma.write64 r10, r8                        ; write 64-byte statistics extension
    xfer14 ehash_enter_record_actions       ; execute matched-record actions

; Timestamp-only continuation. The statistics extension is resident in the
; record workspace on entry. Read the configured MURAM counter, publish it in
; the extension, and join the common DDR writeback path.
;
; Pseudocode:
;   counter_ptr = load_be32(&stats_buffer.timestamp_counter);
;   stats_buffer.timestamp = load_be32(muram_pointer(counter_ptr));
;   goto ehash_writeback_record_stats;
ehash_update_record_timestamp:
    memw.read r13, r8, 0x18                    ; timestamp-only: load timestamp_counter ptr
    memw.read r13, r13, 0x0                    ; timestamp-only: read current counter
    memw.write r13, r8, 0x10                   ; timestamp-only: save timestamp
    xfer14 ehash_writeback_record_stats     ; join at extension writeback

; Decode the record's parameter cursor and preserve parser-result state needed
; by the FE action interpreter.
;
; Pseudocode:
;   record = ic.private_record_workspace;
;   flags = load_be16(&record->flags);
;   param_offset = (flags & 0x3f) << 2;
;   clear_bytes(&ic.key[0x10], 4);
;   copy_bytes(saved_pr, &ic.pr[16], 16);
;   goto ehash_record_action_setup;
ehash_enter_record_actions:
    memw.read r24, r26/*IC*/, 0x98             ; record = record workspace
    memb.read r19, r24, 0x1                    ; flags_lo = low byte of record.flags
    andi16 r19, 0x3f                           ; param_offset_words = flags_lo & 0x3f
    lsl32i r19, 0x2, r19                       ; param_offset = words * 4
    li16 r14, 0x0                              ; zero = 0
    memw.write r14, r26/*IC*/, 0x60            ; clear four bytes in documented KEY area
    memd.read r20, r26/*IC*/, 0x30             ; save IC.PR bytes 16..23
    memd.read r22, r26/*IC*/, 0x38             ; save IC.PR bytes 24..31
    xfer14 ehash_record_action_setup        ; enter FE setup at w8668
    state 32, r28/*FRAME*/, 4, 3               ; carry current-frame state into action path

; A cumulative record contains a header, packed keys at offset 12, and an array
; of eight-byte regular-record addresses beginning at `tbl_entry_index`.
;
; Pseudocode:
;   if (flag_is_set(node->flags, INVALID_CUMULATIVE))
;     goto follow_collision_link;
;   matched = find_packed_key(ic.key, ic.ks, node->data,
;                             node->num_key_entries, &match_index);
;   if (!matched) goto follow_collision_link;
;   if (flag_is_set(node->flags, INVALID_CUMULATIVE))
;     goto follow_collision_link;
;   selected_ptr = (uint8_t *)node + node->tbl_entry_index + match_index * 8;
;   selected_record_addr = load_be64(selected_ptr);
ehash_compare_cumulative_node:
    brbitset16 r9, 6, ehash_follow_collision_link ; skip invalid cumulative node
    addlane8 r26/*IC*/, r16, 3, 0x50           ; key = &IC.KEY[0]
    memb.read r17, r26/*IC*/, 0x1c             ; key_size = IC.KS
    subi16 r17, 0x1                            ; key_last = key_size - 1
    bitfield 0, r17, r16, 24, 48               ; configure key source and length
    addlane8 r8, r18, 3, 0xc                   ; cumulative_keys = node + 12
    memb.read r17, r8, 0x1                     ; key_count = node.num_key_entries
    bitfield 0, r17, r18, 24, 48               ; configure packed-key table and count
    state 31, r31/*COND*/, r31/*COND*/, 16, 32 ; arm table-comparison state
    keycmp.run                                  ; r0 encodes the packed-key match result
    xfer14 w1731                            ; join after comparison
    nop                                        ; fill slot
    lsr32i r0, 0x4, r17                        ; match_index = compare_state >> 4
    memw.read r8, r26/*IC*/, 0x98              ; node = record workspace
    memb.read r20, r8, 0x1                     ; key_count = node.num_key_entries
    cmp32 20, 17, 20                           ; compare(match_index, key_count)
    cbrz16.comp ehash_follow_collision_link    ; no packed key matched
    memb.read r9, r8, 0x0                      ; flags = node.flags
    brbitset16 r9, 6, ehash_follow_collision_link ; reject concurrent invalidation
    lsl32i r17, 0x3, r17                       ; address_index = match_index * 8
    memb.read r20, r8, 0x3                     ; table = node.tbl_entry_index
    add32 r20, r17, r20                        ; selected_offset = table + address_index
    add32 r20, r8, r20                         ; selected_ptr = node + selected_offset

; Fetch the regular record selected by the cumulative node and continue through
; the same accounting/action machinery as a direct regular hit.
;
; Pseudocode:
;   selected_record_addr = load_be64(selected_ptr);
;   ic.private_bucket_head = selected_record_addr;
;   dma_read256(selected_record_addr, record_workspace);
;   if (flag_is_set(load_be16(&record->flags), EN_EHASH_ENTRY_INVALID))
;     goto ehash_miss;
;   goto ehash_acquire_record_stats;
ehash_select_cumulative_record:
    memd.read r18, r20, 0x0                    ; selected_record_addr = load64(selected_ptr)
    memd.write r18, r26/*IC*/, 0x90            ; save selected external address
    immhi16 r27, 0x2000                        ; select 256-byte DMA mode
    memw.read r8, r26/*IC*/, 0x98              ; destination = record workspace
    dma.read256 r18, r8                        ; fetch selected regular record
    wait.cont w1749                             ; suspend until the selected-record DMA completes
    nop                                        ; fill slot
    memw.read r8, r26/*IC*/, 0x98              ; record = record workspace
    memb.read r9, r8, 0x0                      ; flags_hi = record[0]
    brbitset16 r9, 7, ehash_miss               ; regular INVALID bit is a miss
    xfer14 ehash_acquire_record_stats       ; direct backward transfer to word 1671
ehash_unresolved_cumulative_action_stub:
    xfer14 ehash_enter_record_actions       ; separate entry stub; no decoded inbound edge

; Follow the public cumulative/collision link at offset 4 when NEXT is set.
;
; Pseudocode:
;   if (!flag_is_set(node->flags, NEXT_CUMULATIVE)) goto ehash_miss;
;   record_addr = load_be64(&node->next_entry_addr);
;   goto ehash_fetch_record;
ehash_follow_collision_link:
    memb.read r9, r8, 0x0                      ; flags = node.flags
    brbitclr16 r9, 5, ehash_miss               ; chain exhaustion is a miss
    memd.read r18, r8, 0x4                     ; record_addr = node.next_entry_addr
    memd.write r18, r26/*IC*/, 0x90            ; save next external address
    xfer14 ehash_fetch_record               ; fetch next chain element

; Some record modes carry a second link in bytes 0x1e..0x23. This field is not
; present in the public base structures, so the listing keeps a neutral name.
;
; Pseudocode:
;   alternate_addr = load_be48((uint8_t *)record + 0x1e);
;   if (alternate_addr == 0) goto ehash_miss;
;   record_addr = alternate_addr;
;   goto ehash_fetch_record;
ehash_follow_alternate_link:
    memw.read r8, r26/*IC*/, 0x98              ; record = record workspace
    memh.read r18, r8, 0x1e                    ; alternate_addr.high16 = record[0x1e]
    memw.read r19, r8, 0x20                    ; alternate_addr.low32 = record[0x20]
    testor32 r18, r19, 18                      ; test alternate_addr for zero
    cbrz16.comp ehash_miss                     ; null alternate link is a miss
    memd.write r18, r26/*IC*/, 0x90            ; save alternate external address
    xfer14 ehash_fetch_record               ; fetch alternate record

; ==========================================================================
; External-hash miss and alternate-result dispatch (w1766..w1846)
; ==========================================================================
.org 0x6e6

; Handle table-type/parser-result special cases before the descriptor's normal
; miss policy. IC+0x22 and IC+0x21 are bytes in the documented PR field.
;
; Pseudocode:
;   descriptor = muram_pointer(ic.ccbase);
;   if (!bit_is_set(ic.pr[2], 3)) goto default_classifier_result;
;   if (!bit_is_set(ic.pr[1], 6)) goto default_classifier_result;
;   ic.pr[1] = 0;
;   protocol_offset = (uint8_t)(ic.pr[24] + 6);
;   protocol_address = merge8(task.frame_base, protocol_offset);
;   protocol = load_be16(protocol_address);
;   if (protocol == 0x0057) {
;     redispatch_parser(task, HXS_IPV6);
;     return;
;   }
;   if (protocol == 0x0021) {
;     redispatch_parser(task, HXS_IPV4);
;     return;
;   }
;   goto default_classifier_result;
ehash_miss:
    memw.read r0, r26/*IC*/, 0x18              ; descriptor = IC.CCBASE
    memh.read r18, r26/*IC*/, 0x22             ; pr_control = halfword(IC.PR + 2)
    tstandi16 r18, 0x800                       ; test PR special-miss bit
    cbrnz16 ehash_select_miss_action           ; inspect parser/result special case
    xfer14 ehash_default_classifier_result  ; otherwise use ordinary miss handling
    nop                                        ; fill slot
ehash_select_miss_action:
    memb.read r18, r26/*IC*/, 0x21             ; pr_flags = IC.PR[1]
    tstandi16 r18, 0x40                        ; test parser-resume flag
    cbrz16.comp ehash_default_classifier_result ; clear means ordinary miss
    li16 r18, 0x0                              ; zero = 0
    memb.write r18, r26/*IC*/, 0x21            ; consume/clear PR flag
    memb.read r18, r26/*IC*/, 0x38             ; take offset from IC.PR[24]
    addi16 r18, 0x6                            ; advance to protocol halfword
    merge8 r17, r28/*FRAME*/, r18             ; r17 = high24(FRAME) | low8(r18)
    memh.read r18, r17, 0x0                    ; protocol = load16(address)
    cmpi16 r18, 0x21                           ; compare PPP IPv4 protocol 0x0021
    cbrz16.comp w1791                          ; IPv4 resumes Parser HXS 5
    cmpi16 r18, 0x57                           ; compare PPP IPv6 protocol 0x0057
    cbrz16.comp w1786                          ; IPv6 resumes Parser HXS 6
    xfer14 ehash_default_classifier_result  ; unknown protocol uses normal miss
    immhi16 r18, 0x44                          ; begin Parser NIA 0x00440006
    addi16 r18, 0x6                            ; select Parser HXS 6
    task.set_end_nia 18, r18                   ; task.EndNIA = Parser HXS 6
    task.redispatch                            ; submit task to parser state
    nop                                        ; fill slot
    immhi16 r18, 0x44                          ; begin Parser NIA 0x00440005
    addi16 r18, 0x5                            ; select Parser HXS 5
    task.set_end_nia 18, r18                   ; task.EndNIA = Parser HXS 5
    task.redispatch                            ; submit task to parser state
    nop                                        ; fill slot

; Normalize a firmware-private classifier result, then continue to the
; descriptor miss-action switch. Controller action 0x1a is the documented
; Pre-BMI Prepare-to-Enqueue action.
;
; Pseudocode:
;   result_kind = extract_result_kind(&ic.icad);
;   if (result_kind == 2 && ic.private_result != 0) {
;     set_task_fqid(task, ic.private_result);
;     normalized_result = ic.private_result;
;     store_be32(&ic.icad, normalized_result);
;     ic.private_result = 0;
;     clear_result_kind(&ic.icad);
;     redispatch_controller(task, PRE_BMI_PREPARE_TO_ENQUEUE);
;     return;
;   }
;   goto ehash_miss_action_dispatch;
ehash_default_classifier_result:
    memh.read r12, r26/*IC*/, 0x16             ; read ICAD tail/result field
    andlane8 r12, r3, 3, 0xf                   ; extract low result-kind nibble
    cmpi16 r3, 0x2                             ; test private result kind 2
    cbrnz16 ehash_miss_action_dispatch         ; other kinds use descriptor policy
    memw.read r18, r26/*IC*/, 0xb8             ; value = IC.private_result
    cbrz16.comp ehash_miss_action_dispatch     ; no private result means descriptor policy
    task.set_fqid r18, r18                     ; apply private enqueue FQID to active task
    memw.write r18, r26/*IC*/, 0x10            ; publish normalized result in ICAD
    li16 r14, 0x1a                             ; Controller PREPARE_TO_ENQUEUE
    task.set_end_nia 14, r14                   ; task.EndNIA = Controller action 0x1a
    li16 r14, 0x0                              ; zero = 0
    memw.write r14, r26/*IC*/, 0xb8            ; clear private result value
    memh.write r14, r26/*IC*/, 0x16            ; clear private result kind
    task.redispatch                            ; submit task to Prepare-to-Enqueue
    nop                                        ; fill slot

; `miss_action_type` occupies the top two bits of descriptor byte 0. Its four
; documented values are DONE=0, NIA=1, ENQUEUE=2, and DROP=3. `jmptbl4` uses
; the low two bits of r18 to select one of the four two-word table entries.
;
; Pseudocode:
;   action = descriptor->miss_action_type;
;   switch (action & 3) {
;     case DONE:    goto ehash_continue_controller;
;     case NIA:     goto ehash_publish_descriptor_nia;
;     case ENQUEUE: goto ehash_apply_descriptor_context;
;     case DROP:    goto ehash_alternate_result;
;   }
ehash_miss_action_dispatch:
    memb.read r18, r0, 0x0                    ; control = descriptor[0]
    lsr32i r18, 0x6, r18                       ; miss_action_type = control >> 6
    jmptbl4 r18, ehash_miss_action_table       ; PC = table + 2 * (r18 & 3)
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
    nop                                        ; dispatch padding
ehash_miss_action_table:
    xfer14 ehash_continue_controller           ; direct DONE entry stub (0)
    nop                                        ; table alignment/fill
    xfer14 ehash_publish_descriptor_nia        ; direct NIA entry stub (1)
    nop                                        ; table alignment/fill
    xfer14 ehash_apply_descriptor_context      ; direct ENQUEUE/FQID entry stub (2)
    nop                                        ; table alignment/fill
    xfer14 ehash_alternate_result              ; direct DROP entry stub (3)

; DONE continues through Controller action 0x1a.
;
; Pseudocode:
;   redispatch_controller(task, PRE_BMI_PREPARE_TO_ENQUEUE);
ehash_continue_controller:
    li16 r14, 0x1a                             ; Prepare-to-Enqueue action
    task.set_end_nia 14, r14                   ; task.EndNIA = Controller 0x1a
    task.redispatch                            ; submit task to Controller
    nop                                        ; fill slot

; DROP uses Controller action 0x1e, documented as Pre-BMI Discard Frame.
;
; Pseudocode:
;   redispatch_controller(task, PRE_BMI_DISCARD_FRAME);
ehash_alternate_result:
    wait.cont w30                               ; suspend and resume at Controller entry 0x1e
    nop                                        ; fill slot

; NIA takes descriptor word 3 as the complete next-invoked-action value.
;
; Pseudocode:
;   redispatch_nia(task, descriptor->nia);
ehash_publish_descriptor_nia:
    memw.read r19, r0, 0xc                     ; nia = descriptor.nia
    task.set_end_nia 19, r19                   ; task.EndNIA = nia
    task.redispatch                            ; submit task to selected engine/action
    nop                                        ; fill slot

; ENQUEUE interprets descriptor word 3 as FQID/context, applies it to task state,
; and then invokes Prepare-to-Enqueue.
;
; Pseudocode:
;   set_task_fqid(task, descriptor->fqid);
;   redispatch_controller(task, PRE_BMI_PREPARE_TO_ENQUEUE);
ehash_apply_descriptor_context:
    memw.read r19, r0, 0xc                     ; fqid/context = descriptor.fqid
    task.set_fqid r19, r19                     ; task.FQID = descriptor.fqid
    li16 r14, 0x1a                             ; Prepare-to-Enqueue action
    task.set_end_nia 14, r14                   ; task.EndNIA = Controller 0x1a
    task.redispatch                            ; submit task to Prepare-to-Enqueue
    nop                                        ; fill slot

; ==========================================================================
; External-hash record-action entry (w8660..w8682)
; ==========================================================================
.org 0x21d4

; Alternate entry used when the record pointer is already in r8.
;
; Pseudocode:
;   flags = load_be16(&record->flags);
;   param_offset = (flags & 0x3f) << 2;
;   copy_bytes(saved_pr, &ic.pr[16], 16);
;   record = ic.private_record_workspace;
;   goto ehash_derive_opcode_cursor;
ehash_record_entry_with_flag_decode:
    memb.read r19, r8, 0x1                    ; flags_lo = low byte of record.flags
    andi16 r19, 0x3f                          ; param_offset_words = flags_lo & 0x3f
    lsl32i r19, 0x2, r19                      ; param_offset = words * 4
    memd.read r20, r26/*IC*/, 0x30            ; save IC.PR bytes 16..23
    memd.read r22, r26/*IC*/, 0x38            ; save IC.PR bytes 24..31
    memw.read r24, r26/*IC*/, 0x98            ; record = record workspace
    xfer14 ehash_derive_opcode_cursor       ; skip setup already done by caller
    state 32, r28/*FRAME*/, 4, 3              ; bind current-frame state

; Save condition and parser-result fragments into firmware-private FE state.
; The exact bitfield selector equations remain ISA-level unknowns, but this
; block's role is fixed by the two entries converging on cursor derivation.
;
; Pseudocode:
;   ic.private_saved_condition = condition_state;
;   fe_state = ic.private_fe_state;
;   fe_state = pack_action_state(fe_state, saved_pr, current_frame_state);
;   ic.private_fe_state = fe_state;
;   goto ehash_derive_opcode_cursor;
ehash_record_action_setup:
    memw.write r31/*COND*/, r26/*IC*/, 0xc0    ; save condition/special state for FE actions
    memw.read r6, r26/*IC*/, 0xd4             ; fe_state = IC.private_fe_state
    bitfield.xform 24, r22, r8, r5, 1         ; position saved PR/frame-state fields
    bitfield 0, r21, r6, 24, 48               ; select a field from fe_state
    bitfield 8, r22, r6, 16, 32               ; combine another saved PR field
    bitfield.merge 0, r6, r5, r5, 16          ; merge fields into new FE state
    xfer14 ehash_derive_opcode_cursor       ; continue into action interpreter
    memw.write r5, r26/*IC*/, 0xd4             ; IC.private_fe_state = packed state

; The host ABI defines both offsets in the 16-bit record flags:
;   PARAM_OFFSET = (flags & 0x3f) << 2
;   OPC_OFFSET   = ((flags >> 6) & 0x1f) << 2
;
; Pseudocode:
;   flags = load_be16(&record->flags);
;   configure_frame_window(task);
;   param_ptr = (uint8_t *)record + param_offset;
;   opcode_offset = ((flags >> 6) & 0x1f) << 2;
;   opcode_ptr = (uint8_t *)record + opcode_offset;
;   opcode = *opcode_ptr;
ehash_derive_opcode_cursor:
    memh.read r0, r24, 0x0                    ; flags = record.flags
    framewin r28/*FRAME*/, r28/*FRAME*/, 1     ; configure FE frame-data window
    add32 r24, r19, r2                        ; param_ptr = record + param_offset
    asr32i r0, 0x6, r0                        ; shifted_flags = flags >> 6
    bitfield.xform 25, r0, r2, r5, 5          ; opcode_offset = (shifted & 0x1f) << 2
    add32 r24, r5, r17                        ; opcode_ptr = record + opcode_offset
ehash_fetch_action_opcode:
    memb.read r0, r17, 0x0                    ; opcode = *opcode_ptr
