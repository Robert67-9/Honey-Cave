from django.core.management.base import BaseCommand
from mall.models import Branch

# (region, branch_type, name, address, city, phone, landmark, lat, lng)
BRANCHES = [
    # ── Greater Accra ─────────────────────────────────────────────────────────
    ('greater_accra','main',    'Market Accra Central',
     'No. 14 Kwame Nkrumah Avenue, CBD', 'Accra',
     '+233 30 221 0001', 'Near Accra Central Post Office', 5.5502, -0.2174),

    ('greater_accra','express', 'Market Tema Express',
     'Community 1, Harbour Road', 'Tema',
     '+233 30 221 0002', 'Opposite Tema Harbour Gate', 5.6698, -0.0166),

    ('greater_accra','agent',   'Market Madina Agent',
     'Madina Market Road, Kiosk 5', 'Madina',
     '+233 24 900 0010', 'Near Madina Zongo Junction', 5.6760, -0.1690),

    # ── Ashanti ───────────────────────────────────────────────────────────────
    ('ashanti','main',    'Market Kumasi Central',
     'Adum Commercial Street, Plot 22', 'Kumasi',
     '+233 32 202 0001', 'Near Kejetia Market', 6.6885, -1.6244),

    ('ashanti','express', 'Market Asokwa Express',
     'Asokwa Bypass Road, Unit B', 'Kumasi',
     '+233 32 202 0002', 'Beside Asokwa Filling Station', 6.6700, -1.6050),

    ('ashanti','agent',   'Market Bantama Agent',
     'Bantama High Street, Shop 3', 'Kumasi',
     '+233 24 900 0020', 'Near Bantama Police Station', 6.7070, -1.6310),

    # ── Western ───────────────────────────────────────────────────────────────
    ('western','main',    'Market Takoradi',
     'Market Circle, Liberation Road', 'Takoradi',
     '+233 31 202 0001', 'Opposite Takoradi Technical Institute', 4.8845, -1.7554),

    ('western','agent',   'Market Sekondi Agent',
     'Sekondi Market Lane, Shop 2', 'Sekondi',
     '+233 24 900 0030', 'Near Sekondi Post Office', 4.9357, -1.7036),

    # ── Western North ─────────────────────────────────────────────────────────
    ('western_north','main', 'Market Sefwi Wiawso',
     'Main Sefwi Road, Wiawso Town Centre', 'Sefwi Wiawso',
     '+233 24 555 0101', 'Near Sefwi Wiawso Municipal Assembly', 6.2059, -2.4849),

    # ── Central ───────────────────────────────────────────────────────────────
    ('central','main',    'Market Cape Coast',
     'Kotokuraba Road, Plot 8', 'Cape Coast',
     '+233 33 213 0001', 'Near Cape Coast Castle Roundabout', 5.1053, -1.2466),

    ('central','express', 'Market Winneba Express',
     'Winneba Main Street, Shop 4', 'Winneba',
     '+233 24 900 0040', 'Near Winneba Lorry Station', 5.3518, -0.6285),

    # ── Eastern ───────────────────────────────────────────────────────────────
    ('eastern','main',    'Market Koforidua',
     'Accra Road, Koforidua Central', 'Koforidua',
     '+233 34 222 0001', 'Beside Koforidua Polytechnic Junction', 6.0940, -0.2600),

    ('eastern','agent',   'Market Nkawkaw Agent',
     'Nkawkaw Market Area, Booth 7', 'Nkawkaw',
     '+233 24 900 0050', 'Near Nkawkaw STC Station', 6.5533, -0.7650),

    # ── Volta ─────────────────────────────────────────────────────────────────
    ('volta','main',    'Market Ho',
     'Sokode Road, Ho Central', 'Ho',
     '+233 36 202 0001', 'Near Ho Municipal Hospital', 6.6011, 0.4706),

    ('volta','express', 'Market Hohoe Express',
     'Hohoe Market Street, Unit 2', 'Hohoe',
     '+233 24 900 0060', 'Near Hohoe Bus Terminal', 7.1528, 0.4742),

    # ── Oti ───────────────────────────────────────────────────────────────────
    ('oti','main', 'Market Dambai',
     'Dambai Town Road, Plot 4', 'Dambai',
     '+233 24 555 0201', 'Near Dambai District Assembly', 7.9690, 0.1769),

    # ── Bono ──────────────────────────────────────────────────────────────────
    ('bono','main',    'Market Sunyani',
     'Fiapre Road, Sunyani Central', 'Sunyani',
     '+233 35 202 0001', 'Opposite Sunyani Municipal Hospital', 7.3349, -2.3238),

    ('bono','agent',   'Market Berekum Agent',
     'Berekum Market Road, Shop 6', 'Berekum',
     '+233 24 900 0070', 'Near Berekum Municipal Assembly', 7.4528, -2.5858),

    # ── Bono East ─────────────────────────────────────────────────────────────
    ('bono_east','main', 'Market Techiman',
     'Techiman Market Road, Shop 15', 'Techiman',
     '+233 35 209 0001', 'Near Techiman Central Market', 7.5904, -1.9344),

    # ── Ahafo ─────────────────────────────────────────────────────────────────
    ('ahafo','main', 'Market Goaso',
     'Asunafo North Road, Goaso Centre', 'Goaso',
     '+233 24 555 0301', 'Near Goaso District Hospital', 6.8017, -2.5177),

    # ── Northern ──────────────────────────────────────────────────────────────
    ('northern','main',    'Market Tamale',
     'Bolgatanga Road, Central Tamale', 'Tamale',
     '+233 37 202 0001', 'Near Tamale Teaching Hospital Roundabout', 9.4008, -0.8393),

    ('northern','express', 'Market Tamale South Express',
     'Tamale South Market, Unit 3', 'Tamale',
     '+233 24 900 0080', 'Near Tamale South Bus Stop', 9.3801, -0.8521),

    # ── Savannah ──────────────────────────────────────────────────────────────
    ('savannah','main', 'Market Damongo',
     'Damongo Town Centre, Plot 3', 'Damongo',
     '+233 24 555 0401', 'Near Damongo District Assembly', 9.0823, -1.8241),

    # ── North East ────────────────────────────────────────────────────────────
    ('north_east','main', 'Market Nalerigu',
     'Nalerigu Main Road, Shop 7', 'Nalerigu',
     '+233 24 555 0501', 'Near Nalerigu Government Hospital', 10.5233, -0.3617),

    # ── Upper East ────────────────────────────────────────────────────────────
    ('upper_east','main',    'Market Bolgatanga',
     'Zuarungu Road, Bolga Central', 'Bolgatanga',
     '+233 38 202 0001', 'Near Bolgatanga Regional Hospital', 10.7857, -0.8514),

    ('upper_east','express', 'Market Navrongo Express',
     'Navrongo Market Lane, Stall 4', 'Navrongo',
     '+233 24 900 0090', 'Near Navrongo Health Research Centre', 10.8939, -1.0921),

    # ── Upper West ────────────────────────────────────────────────────────────
    ('upper_west','main',  'Market Wa',
     'Wa Central Market Area, Plot 11', 'Wa',
     '+233 39 202 0001', 'Near Wa Central Mosque', 10.0607, -2.5099),

    ('upper_west','agent', 'Market Lawra Agent',
     'Lawra Town Centre, Booth 2', 'Lawra',
     '+233 24 900 0095', 'Near Lawra District Hospital', 10.6325, -2.8996),
]


class Command(BaseCommand):
    help = 'Seed Market branches — all 16 regions, 3 types (Main/Express/Agent)'

    def handle(self, *args, **kwargs):
        created = updated = 0
        for region, btype, name, address, city, phone, landmark, lat, lng in BRANCHES:
            branch, is_new = Branch.objects.update_or_create(
                name=name,
                defaults=dict(
                    region=region, branch_type=btype,
                    address=address, city=city, phone=phone, landmark=landmark,
                    email=f'branch.{city.lower().replace(" ","")}@market.com.gh',
                    latitude=lat, longitude=lng, is_active=True,
                )
            )
            if is_new: created += 1
            else:      updated += 1
            icon = {'main': '🏢', 'express': '⚡', 'agent': '📍'}.get(btype, '🏪')
            self.stdout.write(f'  {"✓" if is_new else "↻"} {icon} {name}')

        self.stdout.write(self.style.SUCCESS(
            f'\n🏪 {created} created, {updated} updated across all 16 regions.\n'
            f'   Types: Main Branch 🏢 · Express Kiosk ⚡ · Mobile Agent 📍'
        ))
