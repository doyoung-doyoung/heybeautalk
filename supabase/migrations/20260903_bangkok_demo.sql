-- Bangkok demo-market data. Apply once after 20260903_initial.sql.
-- Existing Korean demo services are retained but hidden from the public listing.

alter table public.services
  add column if not exists currency text not null default 'THB'
  check (currency in ('THB', 'KRW'));

update public.services set is_active = false where id <= 6;

insert into public.clinics (name, district, rating, description)
select 'Sukhumvit Glow Clinic', 'Watthana, Bangkok', 4.8, 'Bangkok demo clinic for facial contour and skin care'
where not exists (select 1 from public.clinics where name = 'Sukhumvit Glow Clinic')
union all
select 'Siam Skin Lab', 'Pathum Wan, Bangkok', 4.7, 'Bangkok demo clinic for lifting and hydration care'
where not exists (select 1 from public.clinics where name = 'Siam Skin Lab')
union all
select 'Ari Aesthetic Studio', 'Phaya Thai, Bangkok', 4.6, 'Bangkok demo clinic for pigment and acne-scar care'
where not exists (select 1 from public.clinics where name = 'Ari Aesthetic Studio');

insert into public.services (clinic_id, name, category, price, currency, duration, slots)
select (select min(id) from public.clinics where name = 'Sukhumvit Glow Clinic'), 'โบท็อกซ์กราม', '보톡스', 2490, 'THB', '20 นาที', array['2026-09-05T11:00:00+07:00','2026-09-06T14:00:00+07:00','2026-09-08T16:00:00+07:00']
where not exists (select 1 from public.services where name = 'โบท็อกซ์กราม')
union all
select (select min(id) from public.clinics where name = 'Sukhumvit Glow Clinic'), 'ฟิลเลอร์ปาก 1cc', '필러', 7900, 'THB', '40 นาที', array['2026-09-05T15:00:00+07:00','2026-09-07T11:00:00+07:00','2026-09-09T13:00:00+07:00']
where not exists (select 1 from public.services where name = 'ฟิลเลอร์ปาก 1cc')
union all
select (select min(id) from public.clinics where name = 'Siam Skin Lab'), 'ยกกระชับ HIFU 300 shots', '리프팅', 6900, 'THB', '45 นาที', array['2026-09-06T10:30:00+07:00','2026-09-07T14:30:00+07:00','2026-09-10T12:00:00+07:00']
where not exists (select 1 from public.services where name = 'ยกกระชับ HIFU 300 shots')
union all
select (select min(id) from public.clinics where name = 'Siam Skin Lab'), 'Skin Booster', '스킨부스터', 3900, 'THB', '40 นาที', array['2026-09-05T13:00:00+07:00','2026-09-08T11:30:00+07:00','2026-09-10T16:30:00+07:00']
where not exists (select 1 from public.services where name = 'Skin Booster')
union all
select (select min(id) from public.clinics where name = 'Ari Aesthetic Studio'), 'Pico Laser', '레이저', 2200, 'THB', '30 นาที', array['2026-09-05T10:00:00+07:00','2026-09-06T16:00:00+07:00','2026-09-09T14:00:00+07:00']
where not exists (select 1 from public.services where name = 'Pico Laser')
union all
select (select min(id) from public.clinics where name = 'Ari Aesthetic Studio'), 'Acne Scar Laser', '레이저', 4500, 'THB', '50 นาที', array['2026-09-07T13:00:00+07:00','2026-09-08T15:00:00+07:00','2026-09-10T10:00:00+07:00']
where not exists (select 1 from public.services where name = 'Acne Scar Laser');
