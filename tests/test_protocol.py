from app.protocol import parse_line

def main():
    line='[SERIAL][STATUS] frame=100 person_active=1 base=2 face=1 hand=1 face_name=abc gesture=3 gesture_score=0.82 fall_status=NORMAL fall_prob=0.12 enroll_pending=0 enroll_id=- enroll_name=- osd_mode=ALL cfg_print_interval=120'
    ev=parse_line(line)
    assert ev and ev.kind=='status'
    assert ev.data['frame']==100 and ev.data['osd_mode']=='ALL'
    assert abs(ev.data['fall_prob']-0.12)<1e-9

    compact=parse_line('[SERIAL][S] f=101 fs=NM p=1 b=3 fc=1 hc=2 gc=4 gs=0.91 dz=1 dzn=5 dzh=1 dza=1 pzh=0 pza=0 fa=29.7 om=ALL pi=120')
    assert compact and compact.kind=='status'
    assert compact.data['frame']==101 and compact.data['person_active']==1
    assert compact.data['fall_status']=='NM' and compact.data['gesture']==4
    assert compact.data['fps_avg']==29.7 and compact.data['dza']==1

    debug=parse_line('[SERIAL][D] f=101 sv=1 sc=1 sgo=0 sgs=0.8')
    assert debug and debug.kind=='debug'
    assert debug.data['posture_valid']==1 and debug.data['posture_class']==1

    alert=parse_line('[SERIAL][PET_DANGER][ALERT] frame=101 targets=1 hits=1')
    assert alert and alert.kind=='pet_danger_alert' and alert.data['hits']==1
    print('protocol test passed')

if __name__=='__main__': main()
