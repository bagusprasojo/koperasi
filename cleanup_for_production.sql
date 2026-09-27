-- ==============================================================================
-- SQL CLEANUP SCRIPT UNTUK IMPLEMENTASI / GO-LIVE PRODUCTION
-- Sistem Informasi Koperasi & POS Kasir
-- ==============================================================================
-- PERINGATAN PENTING:
-- 1. Script ini akan MENGHAPUS data-data uji coba / transaksi lama secara PERMANEN.
-- 2. Sangat disarankan untuk melakukan BACKUP DATABASE terlebih dahulu sebelum eksekusi!
--
-- CARA BACKUP SEBELUM EKSEKUSI (Buka Terminal / Command Prompt):
-- mysqldump -u root -p koperasi > backup_koperasi_sebelum_cleanup.sql
-- ==============================================================================

USE `koperasi`;

-- Matikan foreign key check sementara agar TRUNCATE dapat mengeksekusi tabel
-- yang memiliki relasi Foreign Key tanpa error dan mereset AUTO_INCREMENT ke 1.
SET FOREIGN_KEY_CHECKS = 0;

-- ==============================================================================
-- BAGIAN 1: PEMBERSIHAN DATA TRANSAKSI PENJUALAN & KASIR (POS)
-- ==============================================================================
TRUNCATE TABLE `sales_receiptprintjob`;
TRUNCATE TABLE `sales_salepayment`;
TRUNCATE TABLE `sales_saleitem`;
TRUNCATE TABLE `sales_sale`;

-- ==============================================================================
-- BAGIAN 2: PEMBERSIHAN DATA TITIPAN / KONSINYASI
-- ==============================================================================
TRUNCATE TABLE `inventory_consignmentbatchitem`;
TRUNCATE TABLE `inventory_consignmentbatch`;

-- ==============================================================================
-- BAGIAN 3: PEMBERSIHAN MUTASI STOK & TRANSAKSI GUDANG
-- ==============================================================================
TRUNCATE TABLE `inventory_stockledger`;
TRUNCATE TABLE `inventory_inventorytransactionitem`;
TRUNCATE TABLE `inventory_inventorytransaction`;

-- ==============================================================================
-- BAGIAN 4: PEMBERSIHAN DATA TUTUP BUKU HARIAN & SNAPSHOT
-- ==============================================================================
TRUNCATE TABLE `inventory_productdailysnapshot`;
TRUNCATE TABLE `inventory_memberdailysnapshot`;
TRUNCATE TABLE `inventory_dailyclosing`;

-- ==============================================================================
-- BAGIAN 5: PEMBERSIHAN TRANSAKSI DEPOSIT ANGGOTA (TOPUP, PENARIKAN, MUTASI)
-- ==============================================================================
TRUNCATE TABLE `members_memberdepositauditlog`;
TRUNCATE TABLE `members_memberledger`;
TRUNCATE TABLE `members_memberwithdrawal`;
TRUNCATE TABLE `members_membertopup`;

-- ==============================================================================
-- BAGIAN 6: PEMBERSIHAN LOG SISTEM & SESI LOGIN LAMA
-- ==============================================================================
TRUNCATE TABLE `django_session`;
TRUNCATE TABLE `django_admin_log`;

-- ==============================================================================
-- BAGIAN 7: RESET SALDO DOMPET ANGGOTA & STOK PRODUK KE NOL
-- (Master Data Anggota dan Produk TETAP AMAN, hanya nilainya dinolkan)
-- ==============================================================================
-- Reset semua saldo anggota menjadi Rp 0.00
UPDATE `members_memberwallet` SET `balance` = 0.00;

-- Reset semua stok fisik produk menjadi 0.000 (disiapkan untuk Stock Opname Perdana)
UPDATE `inventory_product` SET `stock` = 0.000;


-- ==============================================================================
-- BAGIAN 8 (OPSIONAL): RESET TOTAL MASTER DATA (HAPUS PRODUK & ANGGOTA)
-- ==============================================================================
-- HAPUS TANDA KOMENTAR (--) DI BAWAH INI HANYA JIKA Anda ingin menghapus TOTAL
-- seluruh master produk, kategori, pemasok, dan anggota (mulai benar-benar dari 0).
-- Akun Administrator & Staf (auth_user & role groups) TIDAK akan terhapus.
-- ------------------------------------------------------------------------------
-- TRUNCATE TABLE `inventory_productpricetier`;
-- TRUNCATE TABLE `inventory_product`;
-- TRUNCATE TABLE `members_membercard`;
-- TRUNCATE TABLE `members_memberwallet`;
-- TRUNCATE TABLE `inventory_consignor`;
-- TRUNCATE TABLE `members_member`;
-- TRUNCATE TABLE `inventory_supplier`;
-- TRUNCATE TABLE `inventory_category`;
-- TRUNCATE TABLE `inventory_unit`;
-- ------------------------------------------------------------------------------

-- Aktifkan kembali pengecekan Foreign Key
SET FOREIGN_KEY_CHECKS = 1;

-- ==============================================================================
-- VERIFIKASI HASIL PEMBERSIHAN (PASTIKAN SEMUA BERJUMLAH 0)
-- ==============================================================================
SELECT 'sales_sale' AS table_name, COUNT(*) AS total_rows FROM `sales_sale`
UNION ALL
SELECT 'sales_saleitem', COUNT(*) FROM `sales_saleitem`
UNION ALL
SELECT 'sales_salepayment', COUNT(*) FROM `sales_salepayment`
UNION ALL
SELECT 'sales_receiptprintjob', COUNT(*) FROM `sales_receiptprintjob`
UNION ALL
SELECT 'inventory_consignmentbatch', COUNT(*) FROM `inventory_consignmentbatch`
UNION ALL
SELECT 'inventory_consignmentbatchitem', COUNT(*) FROM `inventory_consignmentbatchitem`
UNION ALL
SELECT 'inventory_inventorytransaction', COUNT(*) FROM `inventory_inventorytransaction`
UNION ALL
SELECT 'inventory_inventorytransactionitem', COUNT(*) FROM `inventory_inventorytransactionitem`
UNION ALL
SELECT 'inventory_stockledger', COUNT(*) FROM `inventory_stockledger`
UNION ALL
SELECT 'inventory_dailyclosing', COUNT(*) FROM `inventory_dailyclosing`
UNION ALL
SELECT 'inventory_productdailysnapshot', COUNT(*) FROM `inventory_productdailysnapshot`
UNION ALL
SELECT 'inventory_memberdailysnapshot', COUNT(*) FROM `inventory_memberdailysnapshot`
UNION ALL
SELECT 'members_membertopup', COUNT(*) FROM `members_membertopup`
UNION ALL
SELECT 'members_memberwithdrawal', COUNT(*) FROM `members_memberwithdrawal`
UNION ALL
SELECT 'members_memberledger', COUNT(*) FROM `members_memberledger`
UNION ALL
SELECT 'members_memberdepositauditlog', COUNT(*) FROM `members_memberdepositauditlog`
UNION ALL
SELECT 'django_session', COUNT(*) FROM `django_session`
UNION ALL
SELECT 'django_admin_log', COUNT(*) FROM `django_admin_log`;
