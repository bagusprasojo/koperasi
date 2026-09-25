from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter(name='clean_number')
def clean_number(value):
    """
    Format angka: hilangkan angka 0 di belakang koma jika bilangan bulat.
    Jika pecahan, tampilkan digit desimal yang relevan (maks 3 digit).
    Pemisah ribuan menggunakan titik (.) dan desimal menggunakan koma (,).
    Contoh:
      20.000 -> '20'
      1500.00 -> '1.500'
      1500.50 -> '1.500,5'
      0.500 -> '0,5'
      0.000 -> '0'
      -5000.00 -> '-5.000'
    """
    if value is None or value == '':
        return '0'
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)

    is_neg = d < 0
    d_abs = abs(d)

    if d_abs % Decimal('1') == Decimal('0'):
        int_val = int(d_abs)
        formatted = f"{int_val:,}".replace(',', '.')
        return f"-{formatted}" if is_neg else formatted

    # Ada nilai pecahan
    formatted = f"{d_abs:f}".rstrip('0').rstrip('.')
    if '.' in formatted:
        integer_part, decimal_part = formatted.split('.', 1)
        int_formatted = f"{int(integer_part):,}".replace(',', '.')
        result = f"{int_formatted},{decimal_part}"
    else:
        result = f"{int(formatted):,}".replace(',', '.')
    return f"-{result}" if is_neg else result


@register.filter(name='clean_qty')
def clean_qty(value):
    """
    Khusus kuantitas/stok barang:
      20.000 -> '20'
      15.000 -> '15'
      0.500 -> '0,5'
    """
    return clean_number(value)


@register.filter(name='format_rupiah')
def format_rupiah(value):
    """
    Format mata uang rupiah:
      2000.00 -> 'Rp 2.000'
      25000.00 -> 'Rp 25.000'
      25000.50 -> 'Rp 25.000,50'
      -5000.00 -> '-Rp 5.000'
    """
    if value is None or value == '':
        return 'Rp 0'
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return f"Rp {value}"

    is_neg = d < 0
    d_abs = abs(d)

    if d_abs % Decimal('1') == Decimal('0'):
        int_val = int(d_abs)
        formatted = f"{int_val:,}".replace(',', '.')
        return f"-Rp {formatted}" if is_neg else f"Rp {formatted}"

    formatted = f"{d_abs:.2f}".rstrip('0').rstrip('.')
    if '.' in formatted:
        integer_part, decimal_part = formatted.split('.', 1)
        int_formatted = f"{int(integer_part):,}".replace(',', '.')
        result = f"{int_formatted},{decimal_part}"
    else:
        result = f"{int(formatted):,}".replace(',', '.')
    return f"-Rp {result}" if is_neg else f"Rp {result}"


@register.filter(name='multiply_qty')
def multiply_qty(price, qty):
    """
    Menghitung price * qty dan memformat sebagai format_rupiah.
    """
    try:
        p = Decimal(str(price or 0))
        q = Decimal(str(qty or 0))
        total = p * q
        return format_rupiah(total)
    except Exception:
        return 'Rp 0'
