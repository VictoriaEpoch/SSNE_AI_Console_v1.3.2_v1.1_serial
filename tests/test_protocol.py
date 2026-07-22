from app.protocol import parse_line

def main():
    line='[SERIAL][STATUS] frame=100 person_active=1 base=2 face=1 hand=1 face_name=abc gesture=3 gesture_score=0.82 fall_status=NORMAL fall_prob=0.12 enroll_pending=0 enroll_id=- enroll_name=- osd_mode=ALL cfg_print_interval=120'
    ev=parse_line(line)
    assert ev and ev.kind=='status'
    assert ev.data['frame']==100 and ev.data['osd_mode']=='ALL'
    assert abs(ev.data['fall_prob']-0.12)<1e-9
    print('protocol test passed')

if __name__=='__main__': main()
