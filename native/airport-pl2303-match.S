/* Offline candidate for A1392 7.6.2 ulpt_match only.
 * Matches this PL2303 (067b:2303, bcdDevice 0300), configuration 1/interface 0.
 * The usb_attach_arg offsets match NetBSD 4/o32 and Apple's original iface load.
 * This dedicated candidate REPLACES printer matching; it is not a full driver.
 * No live kernel writes are performed by this file or its test harness.
 */
    .set noreorder
    .text
    .align 2
    .globl airport_pl2303_match
    .ent airport_pl2303_match
airport_pl2303_match:
    lw      $8, 28($6)          /* iface: reject whole-device matching */
    beq     $8, $0, no_match
     move   $2, $0
    lw      $8, 12($6)          /* vendor */
    addiu   $9, $0, 0x067b
    bne     $8, $9, no_match
     lw     $8, 16($6)          /* product */
    addiu   $9, $0, 0x2303
    bne     $8, $9, no_match
     lw     $8, 20($6)          /* release */
    addiu   $9, $0, 0x0300
    bne     $8, $9, no_match
     lw     $8, 4($6)           /* configuration number */
    addiu   $9, $0, 1
    bne     $8, $9, no_match
     lw     $8, 8($6)           /* interface number */
    bne     $8, $0, no_match
     nop
    jr      $31
     addiu  $2, $0, 8           /* UMATCH_VENDOR_PRODUCT_CONF_IFACE */
no_match:
    jr      $31
     nop
    .end airport_pl2303_match
