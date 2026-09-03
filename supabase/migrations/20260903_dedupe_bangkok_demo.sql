-- Keep the first copy of each Bangkok demo service public and preserve later
-- copies as inactive records for auditability.
with ranked as (
  select id,
         row_number() over (partition by name order by id) as duplicate_rank
  from public.services
  where name in (
    'โบท็อกซ์กราม', 'ฟิลเลอร์ปาก 1cc', 'ยกกระชับ HIFU 300 shots',
    'Skin Booster', 'Pico Laser', 'Acne Scar Laser'
  )
)
update public.services
set is_active = false
where id in (select id from ranked where duplicate_rank > 1);
