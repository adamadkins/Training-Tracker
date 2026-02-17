from datetime import datetime, timezone, timedelta
import re

import flask
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, abort
)
from flask_login import login_required, current_user
from sqlalchemy import or_, and_, func, case
from sqlalchemy.orm import aliased

from app import db
from app.models import (
    Employee, Message, MessageReaction, Channel, ChannelParticipant,
    SystemSettings, TrainingSession, User
)
from app.utils.notifications import notify

messages_bp = Blueprint('messages', __name__, url_prefix='/messages')


@messages_bp.before_app_request
def load_system_settings():
    """Load system-wide settings into g for every request."""
    flask.g.system_settings = SystemSettings.query.first()


# ── Helpers ──────────────────────────────────────────────

def get_reaction_map(message_ids, current_employee_id):
    """Build a reaction summary keyed by message ID."""
    if not message_ids:
        return {}

    reactions = MessageReaction.query.filter(
        MessageReaction.message_id.in_(message_ids)
    ).all()

    raw = {}
    for r in reactions:
        raw.setdefault(r.message_id, {})
        raw[r.message_id].setdefault(r.emoji, {'count': 0, 'me': False})
        raw[r.message_id][r.emoji]['count'] += 1
        if r.employee_id == current_employee_id:
            raw[r.message_id][r.emoji]['me'] = True

    result = {}
    for msg_id, emojis in raw.items():
        result[msg_id] = [
            {'emoji': char, 'count': data['count'], 'me': data['me']}
            for char, data in emojis.items()
        ]
    return result


def enrich_messages_with_sessions(messages):
    """
    Scan messages for [SESSION_LINK:ID] patterns and return
    a map of session_id -> TrainingSession object.
    """
    session_ids = []
    for m in messages:
        match = re.search(r'\[SESSION_LINK:(\d+)\]', m.body)
        if match:
            session_ids.append(int(match.group(1)))

    if not session_ids:
        return {}

    sessions = TrainingSession.query.filter(
        TrainingSession.id.in_(session_ids)
    ).all()
    return {s.id: s for s in sessions}


def mark_as_read(partner_id, my_id):
    """Mark all unread messages from partner as read."""
    Message.query.filter(
        Message.sender_id == partner_id,
        Message.recipient_id == my_id,
        Message.read_at == None
    ).update({Message.read_at: datetime.now(timezone.utc)})
    db.session.commit()


def _body_snippet(body, max_len=80):
    """Return a display snippet, stripping [SESSION_LINK:...] and truncating."""
    if not body:
        return ''
    text = re.sub(r'\s*\[SESSION_LINK:\d+\].*', '', body).strip() or '(attachment)'
    return (text[:max_len] + '…') if len(text) > max_len else text


def get_reply_to_map(messages):
    """Build reply_to_id -> {id, body_snippet, sender_name} for messages that have reply_to_id."""
    parent_ids = list({m.reply_to_id for m in messages if m.reply_to_id})
    if not parent_ids:
        return {}
    parents = Message.query.filter(Message.id.in_(parent_ids)).options(
        db.joinedload(Message.sender)
    ).all()
    return {
        p.id: {
            'id': p.id,
            'body': _body_snippet(p.body),
            'sender_name': (p.sender.first_name + ' ' + p.sender.last_name) if p.sender else 'Unknown',
        }
        for p in parents
    }


def get_or_create_dm_channel(my_id, partner_id):
    """Find or create a DM channel between two employees. Returns (Channel, created)."""
    if my_id == partner_id:
        return None, False
    my_ch = db.session.query(ChannelParticipant.channel_id).filter(ChannelParticipant.employee_id == my_id)
    other_ch = db.session.query(ChannelParticipant.channel_id).filter(ChannelParticipant.employee_id == partner_id)
    existing = Channel.query.filter(
        Channel.channel_type == 'dm',
        Channel.id.in_(my_ch),
        Channel.id.in_(other_ch),
    ).first()
    if existing:
        return existing, False
    ch = Channel(name=None, channel_type='dm', created_by_id=my_id)
    db.session.add(ch)
    db.session.flush()
    db.session.add(ChannelParticipant(channel_id=ch.id, employee_id=my_id))
    db.session.add(ChannelParticipant(channel_id=ch.id, employee_id=partner_id))
    db.session.commit()
    return ch, True


def _channel_display_name(channel, my_id):
    """Display name for a channel: name for group, or other participant's name for DM."""
    if channel.name:
        return channel.name
    participants = list(channel.participants)
    others = [p.employee for p in participants if p.employee_id != my_id]
    if not others:
        return "Direct Message"
    if len(others) == 1:
        return others[0].first_name + " " + others[0].last_name
    return ", ".join(o.first_name + " " + o.last_name for o in others[:2]) + (" …" if len(others) > 2 else "")


def _channel_avatar(channel, my_id):
    """For template: return first other participant for DM (for avatar), else None."""
    if channel.name:
        return None
    participants = list(channel.participants)
    others = [p.employee for p in participants if p.employee_id != my_id]
    return others[0] if len(others) == 1 else None


def _build_sidebar_data(my_id):
    """Build sidebar channel + DM lists for the unified messaging layout."""
    Partner = aliased(Employee)
    _min_ts = datetime(2000, 1, 1, tzinfo=timezone.utc)

    # ── Channels I'm in (DM + group) ──
    my_participations = ChannelParticipant.query.filter_by(employee_id=my_id).all()
    sidebar_channels = []
    sidebar_dms = []
    for cp in my_participations:
        ch = cp.channel
        last_msg = Message.query.filter_by(channel_id=ch.id).order_by(Message.timestamp.desc()).first()
        unread = 0
        if cp.last_read_at:
            unread = Message.query.filter(
                Message.channel_id == ch.id,
                Message.timestamp > cp.last_read_at,
                Message.sender_id != my_id,
            ).count()
        else:
            unread = Message.query.filter(
                Message.channel_id == ch.id,
                Message.sender_id != my_id,
            ).count()
        item = {
            'type': 'channel',
            'channel_type': ch.channel_type,
            'channel': ch,
            'last_message': _body_snippet(last_msg.body) if last_msg else '',
            'timestamp': last_msg.timestamp if last_msg else ch.created_at,
            'unread': unread,
            'display_name': _channel_display_name(ch, my_id),
            'avatar_employee': _channel_avatar(ch, my_id),
        }
        if ch.channel_type == 'channel':
            sidebar_channels.append(item)
        else:
            sidebar_dms.append(item)

    # ── Legacy 1:1 DMs (no channel_id) ──
    other_id_expr = case(
        (Message.sender_id == my_id, Message.recipient_id),
        else_=Message.sender_id
    ).label("other_id")
    sq = db.session.query(
        other_id_expr,
        func.max(Message.id).label('last_msg_id'),
        func.sum(
            case(
                (and_(Message.recipient_id == my_id, Message.read_at == None), 1),
                else_=0
            )
        ).label('unread_count')
    ).filter(
        or_(Message.sender_id == my_id, Message.recipient_id == my_id),
        Message.channel_id == None,
    ).group_by("other_id").subquery()

    rows = db.session.query(Partner, Message, sq.c.unread_count) \
        .join(Partner, Partner.id == sq.c.other_id) \
        .join(Message, Message.id == sq.c.last_msg_id) \
        .order_by(Message.timestamp.desc()).all()

    for partner, last_msg, unread in rows:
        sidebar_dms.append({
            'type': 'dm_legacy',
            'channel_type': 'dm',
            'partner': partner,
            'last_message': _body_snippet(last_msg.body) or '',
            'timestamp': last_msg.timestamp,
            'unread': int(unread) if unread else 0,
            'display_name': partner.first_name + ' ' + partner.last_name,
            'avatar_employee': partner,
        })

    sidebar_channels.sort(key=lambda x: x['display_name'].lower())
    sidebar_dms.sort(key=lambda x: x['timestamp'] or _min_ts, reverse=True)

    unreads = [item for item in sidebar_channels + sidebar_dms if item.get('unread', 0) > 0]
    unreads.sort(key=lambda x: x.get('timestamp') or _min_ts, reverse=True)

    directory = Employee.query.filter(
        Employee.id != my_id,
        Employee.status == 'active'
    ).order_by(Employee.first_name).all()

    return sidebar_channels, sidebar_dms, unreads, directory


# ── Routes ───────────────────────────────────────────────

@messages_bp.route('/')
@login_required
def inbox():
    if not current_user.employee_id:
        flash("Link your account to an employee profile to chat.", "warning")
        return redirect(url_for('main.index'))
    my_id = current_user.employee_id
    sidebar_channels, sidebar_dms, sidebar_unreads, directory = _build_sidebar_data(my_id)
    return render_template(
        'messages/messages.html',
        sidebar_channels=sidebar_channels,
        sidebar_dms=sidebar_dms,
        sidebar_unreads=sidebar_unreads,
        directory=directory,
        active_channel=None,
        active_partner=None,
        channel_display_name='',
        channel_avatar_employee=None,
        participants=[],
        messages=[],
        reactions={},
        session_map={},
        reply_to_map={},
        now_date='',
        yesterday_date='',
        can_edit_channel=False,
    )


@messages_bp.route('/<int:partner_id>', methods=['GET', 'POST'])
@login_required
def chat(partner_id):
    if not current_user.employee_id:
        abort(403)

    my_id = current_user.employee_id
    partner = Employee.query.get_or_404(partner_id)

    if flask.g.system_settings and not flask.g.system_settings.dm_enabled:
        flash("Messaging is disabled.", "info")
        return redirect(url_for('messages.inbox'))

    # Handle new message
    if request.method == 'POST':
        body = request.form.get('body', '').strip()
        if body:
            reply_to_id = None
            rt = request.form.get('reply_to_id') or (request.get_json(silent=True) or {}).get('reply_to_id')
            if rt is not None:
                try:
                    reply_to_id = int(rt)
                except (TypeError, ValueError):
                    reply_to_id = None
            if reply_to_id is not None:
                parent = Message.query.filter(
                    Message.id == reply_to_id,
                    or_(
                        and_(Message.sender_id == my_id, Message.recipient_id == partner_id),
                        and_(Message.sender_id == partner_id, Message.recipient_id == my_id),
                    ),
                ).first()
                if not parent:
                    reply_to_id = None
            db.session.add(Message(
                sender_id=my_id,
                recipient_id=partner_id,
                body=body,
                reply_to_id=reply_to_id,
            ))
            db.session.commit()

            # Email-only notification for DM recipient
            sender_emp = Employee.query.get(my_id)
            sender_name = (sender_emp.first_name + ' ' + sender_emp.last_name) if sender_emp else 'Someone'
            snippet = (body[:80] + '...') if len(body) > 80 else body
            recipient_user = User.query.filter_by(employee_id=partner_id).first()
            if recipient_user:
                notify(recipient_user,
                       f"New message from {sender_name}",
                       snippet,
                       category='message',
                       link_url=url_for('messages.chat', partner_id=my_id, _external=True),
                       email_only=True)
                db.session.commit()

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'status': 'success'})
            return redirect(url_for('messages.chat', partner_id=partner_id))

    # Mark incoming messages as read
    mark_as_read(partner_id, my_id)

    # Load conversation
    messages = Message.query.filter(
        or_(
            and_(Message.sender_id == my_id, Message.recipient_id == partner_id),
            and_(Message.sender_id == partner_id, Message.recipient_id == my_id),
        )
    ).order_by(Message.timestamp.asc()).all()

    reaction_map = get_reaction_map([m.id for m in messages], my_id)
    session_map = enrich_messages_with_sessions(messages)
    reply_to_map = get_reply_to_map(messages)

    # Date helpers for template
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    sidebar_channels, sidebar_dms, sidebar_unreads, directory = _build_sidebar_data(my_id)
    return render_template(
        'messages/messages.html',
        sidebar_channels=sidebar_channels,
        sidebar_dms=sidebar_dms,
        sidebar_unreads=sidebar_unreads,
        directory=directory,
        active_channel=None,
        active_partner=partner,
        channel_display_name='',
        channel_avatar_employee=None,
        participants=[],
        messages=messages,
        reactions=reaction_map,
        session_map=session_map,
        reply_to_map=reply_to_map,
        now_date=today.strftime('%Y-%m-%d'),
        yesterday_date=yesterday.strftime('%Y-%m-%d'),
        can_edit_channel=False,
    )


@messages_bp.route('/dm/<int:partner_id>')
@login_required
def dm_redirect(partner_id):
    """Find or create DM channel with partner and redirect to channel chat."""
    if not current_user.employee_id:
        abort(403)
    partner = Employee.query.get_or_404(partner_id)
    if flask.g.system_settings and not flask.g.system_settings.dm_enabled:
        flash("Messaging is disabled.", "info")
        return redirect(url_for('messages.inbox'))
    ch, _ = get_or_create_dm_channel(current_user.employee_id, partner_id)
    if not ch:
        flash("Cannot start a chat with yourself.", "warning")
        return redirect(url_for('messages.inbox'))
    return redirect(url_for('messages.channel_chat', channel_id=ch.id))


@messages_bp.route('/channel/<int:channel_id>', methods=['GET', 'POST'])
@login_required
def channel_chat(channel_id):
    if not current_user.employee_id:
        abort(403)
    my_id = current_user.employee_id
    ch = Channel.query.get_or_404(channel_id)
    participation = ChannelParticipant.query.filter_by(channel_id=channel_id, employee_id=my_id).first()
    if not participation:
        flash("You are not in this channel.", "warning")
        return redirect(url_for('messages.inbox'))

    if flask.g.system_settings and not flask.g.system_settings.dm_enabled:
        flash("Messaging is disabled.", "info")
        return redirect(url_for('messages.inbox'))

    # POST: send message
    if request.method == 'POST':
        body = request.form.get('body', '').strip()
        if body:
            if getattr(ch, 'is_read_only', False):
                can_post = (
                    current_user.role == 'manager' or
                    (ch.created_by_id and ch.created_by_id == my_id)
                )
                if not can_post:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'error': 'This channel is read-only. Only the channel creator and managers can post.'}), 403
                    flash('This channel is read-only. Only the channel creator and managers can post.', 'warning')
                    return redirect(url_for('messages.channel_chat', channel_id=channel_id))
            reply_to_id = request.form.get('reply_to_id') or (request.get_json(silent=True) or {}).get('reply_to_id')
            if reply_to_id is not None:
                try:
                    reply_to_id = int(reply_to_id)
                except (TypeError, ValueError):
                    reply_to_id = None
            if reply_to_id:
                parent = Message.query.filter_by(id=reply_to_id, channel_id=channel_id).first()
                if not parent:
                    reply_to_id = None
            db.session.add(Message(
                sender_id=my_id,
                recipient_id=my_id,
                channel_id=channel_id,
                body=body,
                reply_to_id=reply_to_id,
            ))
            db.session.commit()

            # Email-only notification for other channel participants
            sender_emp = Employee.query.get(my_id)
            sender_name = (sender_emp.first_name + ' ' + sender_emp.last_name) if sender_emp else 'Someone'
            ch_name = _channel_display_name(ch, my_id)
            snippet = (body[:80] + '...') if len(body) > 80 else body
            ch_link = url_for('messages.channel_chat', channel_id=channel_id, _external=True)
            for cp in ch.participants:
                if cp.employee_id != my_id:
                    u = User.query.filter_by(employee_id=cp.employee_id).first()
                    if u:
                        notify(u,
                               f"New message in {ch_name}",
                               f"{sender_name}: {snippet}",
                               category='message',
                               link_url=ch_link,
                               email_only=True)
            db.session.commit()

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'status': 'success'})
            return redirect(url_for('messages.channel_chat', channel_id=channel_id))

    # Mark channel as read
    participation.last_read_at = datetime.now(timezone.utc)
    db.session.commit()

    messages = Message.query.filter_by(channel_id=channel_id).options(
        db.joinedload(Message.sender)
    ).order_by(Message.timestamp.asc()).all()
    reaction_map = get_reaction_map([m.id for m in messages], my_id)
    session_map = enrich_messages_with_sessions(messages)
    reply_to_map = get_reply_to_map(messages)
    participants = [p.employee for p in ch.participants]
    others = [e for e in participants if e.id != my_id]
    channel_display_name = ch.name if ch.name else (others[0].first_name + ' ' + others[0].last_name if len(others) == 1 else ', '.join(e.first_name for e in others[:2]) + (' …' if len(others) > 2 else ''))
    channel_avatar_employee = others[0] if len(others) == 1 else None
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    can_edit_channel = (
        ch.channel_type == 'channel' and
        (current_user.role == 'manager' or (ch.created_by_id and ch.created_by_id == my_id))
    )

    sidebar_channels, sidebar_dms, sidebar_unreads, directory = _build_sidebar_data(my_id)
    return render_template(
        'messages/messages.html',
        sidebar_channels=sidebar_channels,
        sidebar_dms=sidebar_dms,
        sidebar_unreads=sidebar_unreads,
        directory=directory,
        active_channel=ch,
        active_partner=None,
        channel_display_name=channel_display_name,
        channel_avatar_employee=channel_avatar_employee,
        participants=participants,
        messages=messages,
        reactions=reaction_map,
        session_map=session_map,
        reply_to_map=reply_to_map,
        now_date=today.strftime('%Y-%m-%d'),
        yesterday_date=yesterday.strftime('%Y-%m-%d'),
        can_edit_channel=can_edit_channel,
    )


@messages_bp.route('/channel/<int:channel_id>/updates')
@login_required
def channel_updates(channel_id):
    my_id = current_user.employee_id
    if not ChannelParticipant.query.filter_by(channel_id=channel_id, employee_id=my_id).first():
        return jsonify({'messages': [], 'reactions': {}})
    last_id = request.args.get('last_id', 0, type=int)
    participation = ChannelParticipant.query.filter_by(channel_id=channel_id, employee_id=my_id).first()
    if participation:
        participation.last_read_at = datetime.now(timezone.utc)
        db.session.commit()

    new_msgs = Message.query.filter(
        Message.channel_id == channel_id,
        Message.id > last_id
    ).options(db.joinedload(Message.sender)).order_by(Message.timestamp.asc()).all()
    session_objects = enrich_messages_with_sessions(new_msgs)
    reply_to_map = get_reply_to_map(new_msgs)
    messages_data = []
    for m in new_msgs:
        session_data = None
        match = re.search(r'\[SESSION_LINK:(\d+)\]', m.body)
        if match:
            s = session_objects.get(int(match.group(1)))
            if s:
                session_data = {
                    'id': s.id,
                    'position': s.position.name,
                    'date': s.session_date.strftime('%b %d'),
                    'status': 'completed' if s.completed_at else 'upcoming',
                    'icon': 'fa-circle-check' if s.completed_at else 'fa-clock',
                    'is_locked': s.is_locked,
                }
        reply_to_data = reply_to_map.get(m.reply_to_id) if m.reply_to_id else None
        sender = m.sender
        messages_data.append({
            'id': m.id,
            'sender_id': m.sender_id,
            'sender_name': (sender.first_name + ' ' + sender.last_name) if sender else 'Unknown',
            'sender_avatar': sender.avatar(36) if sender else '',
            'body': m.body,
            'timestamp': m.timestamp.isoformat(),
            'read_at': m.read_at.isoformat() if m.read_at else None,
            'session_preview': session_data,
            'reply_to': reply_to_data,
        })
    recent_msgs = Message.query.filter_by(channel_id=channel_id).order_by(Message.timestamp.desc()).limit(50).all()
    reaction_map = get_reaction_map([m.id for m in recent_msgs], my_id)
    return jsonify({'messages': messages_data, 'reactions': reaction_map})


@messages_bp.route('/channels', methods=['POST'])
@login_required
def create_channel():
    if not current_user.employee_id:
        return jsonify({'error': 'Not allowed'}), 403
    if flask.g.system_settings and not flask.g.system_settings.dm_enabled:
        return jsonify({'error': 'Messaging is disabled'}), 400
    data = request.get_json() or request.form
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip() or None
    is_private = data.get('is_private') in (True, 'true', '1', 1)
    is_read_only = data.get('is_read_only') in (True, 'true', '1', 1)
    member_ids = data.get('member_ids') or data.get('member_ids[]') or []
    if isinstance(member_ids, str):
        member_ids = [member_ids]
    member_ids = [int(x) for x in member_ids if x]
    if not name:
        return jsonify({'error': 'Channel name is required'}), 400
    ch = Channel(
        name=name,
        channel_type='channel',
        description=description,
        is_private=is_private,
        is_read_only=is_read_only,
        created_by_id=current_user.employee_id,
    )
    db.session.add(ch)
    db.session.flush()
    db.session.add(ChannelParticipant(channel_id=ch.id, employee_id=current_user.employee_id))
    for eid in member_ids:
        if eid != current_user.employee_id:
            db.session.add(ChannelParticipant(channel_id=ch.id, employee_id=eid))
    db.session.commit()
    return jsonify({'channel_id': ch.id, 'url': url_for('messages.channel_chat', channel_id=ch.id)})


@messages_bp.route('/channel/<int:channel_id>/update', methods=['POST'])
@login_required
def channel_update(channel_id):
    if not current_user.employee_id:
        return jsonify({'error': 'Not allowed'}), 403
    ch = Channel.query.get_or_404(channel_id)
    if ch.channel_type != 'channel':
        return jsonify({'error': 'Only group channels can be edited'}), 400
    participation = ChannelParticipant.query.filter_by(channel_id=channel_id, employee_id=current_user.employee_id).first()
    if not participation:
        return jsonify({'error': 'You are not in this channel'}), 403
    can_edit = current_user.role == 'manager' or (ch.created_by_id == current_user.employee_id)
    if not can_edit:
        return jsonify({'error': 'Only the channel creator or managers can edit settings'}), 403

    data = request.get_json() or request.form
    name = (data.get('name') or '').strip()
    if name:
        ch.name = name
    ch.description = (data.get('description') or '').strip() or None
    ch.is_private = data.get('is_private') in (True, 'true', '1', 1)
    ch.is_read_only = data.get('is_read_only') in (True, 'true', '1', 1)

    member_ids = data.get('member_ids') or data.get('member_ids[]') or []
    if isinstance(member_ids, str):
        member_ids = [member_ids]
    member_ids = [int(x) for x in member_ids if x]
    current_ids = {p.employee_id for p in ch.participants}
    new_ids = set(member_ids)
    for eid in new_ids - current_ids:
        db.session.add(ChannelParticipant(channel_id=ch.id, employee_id=eid))
    for cp in list(ch.participants):
        if cp.employee_id not in new_ids and cp.employee_id != ch.created_by_id:
            db.session.delete(cp)
    db.session.commit()
    return jsonify({'status': 'ok', 'url': url_for('messages.channel_chat', channel_id=ch.id)})


@messages_bp.route('/<int:partner_id>/updates')
@login_required
def chat_updates(partner_id):
    my_id = current_user.employee_id
    last_id = request.args.get('last_id', 0, type=int)

    mark_as_read(partner_id, my_id)

    new_msgs = Message.query.filter(
        or_(
            and_(Message.sender_id == my_id, Message.recipient_id == partner_id),
            and_(Message.sender_id == partner_id, Message.recipient_id == my_id),
        ),
        Message.id > last_id
    ).options(db.joinedload(Message.sender)).order_by(Message.timestamp.asc()).all()

    session_objects = enrich_messages_with_sessions(new_msgs)
    reply_to_map = get_reply_to_map(new_msgs)

    messages_data = []
    for m in new_msgs:
        session_data = None
        match = re.search(r'\[SESSION_LINK:(\d+)\]', m.body)
        if match:
            s = session_objects.get(int(match.group(1)))
            if s:
                session_data = {
                    'id': s.id,
                    'position': s.position.name,
                    'date': s.session_date.strftime('%b %d'),
                    'status': 'completed' if s.completed_at else 'upcoming',
                    'icon': 'fa-circle-check' if s.completed_at else 'fa-clock',
                    'is_locked': s.is_locked,
                }

        reply_to_data = reply_to_map.get(m.reply_to_id) if m.reply_to_id else None
        sender = m.sender

        messages_data.append({
            'id': m.id,
            'sender_id': m.sender_id,
            'sender_name': (sender.first_name + ' ' + sender.last_name) if sender else 'Unknown',
            'sender_avatar': sender.avatar(36) if sender else '',
            'body': m.body,
            'timestamp': m.timestamp.isoformat(),
            'read_at': m.read_at.isoformat() if m.read_at else None,
            'session_preview': session_data,
            'reply_to': reply_to_data,
        })

    # Reactions for recent messages (for live updates)
    recent_msgs = Message.query.filter(
        or_(
            and_(Message.sender_id == my_id, Message.recipient_id == partner_id),
            and_(Message.sender_id == partner_id, Message.recipient_id == my_id),
        )
    ).order_by(Message.timestamp.desc()).limit(50).all()

    reaction_map = get_reaction_map([m.id for m in recent_msgs], my_id)

    return jsonify({'messages': messages_data, 'reactions': reaction_map})


@messages_bp.route('/api/sessions/<int:partner_id>')
@login_required
def get_shared_sessions(partner_id):
    """Return sessions available for attachment in chat."""
    query = TrainingSession.query

    if current_user.role == 'manager':
        sessions = query.order_by(
            TrainingSession.session_date.desc()
        ).limit(100).all()
    elif current_user.role == 'trainer':
        sessions = query.filter(
            TrainingSession.trainer_employee_id == current_user.employee_id
        ).order_by(TrainingSession.session_date.desc()).limit(100).all()
    else:
        sessions = query.filter(
            or_(
                and_(
                    TrainingSession.trainee_employee_id == partner_id,
                    TrainingSession.trainer_employee_id == current_user.employee_id,
                ),
                and_(
                    TrainingSession.trainer_employee_id == partner_id,
                    TrainingSession.trainee_employee_id == current_user.employee_id,
                ),
            )
        ).order_by(TrainingSession.session_date.desc()).all()

    return jsonify([{
        'id': s.id,
        'position': s.position.name,
        'date': s.session_date.strftime('%b %d'),
        'role': s.trainee.first_name + " " + s.trainee.last_name,
        'status': 'completed' if s.completed_at else 'upcoming',
        'status_icon': 'fa-circle-check' if s.completed_at else 'fa-clock',
        'is_locked': s.is_locked,
    } for s in sessions])


@messages_bp.route('/api/unread_count')
@login_required
def api_unread_count():
    if not current_user.employee_id:
        return jsonify({'count': 0})
    my_id = current_user.employee_id
    # Legacy 1:1 unread (one query)
    count = Message.query.filter(
        Message.recipient_id == my_id,
        Message.read_at == None,
        Message.channel_id == None,
    ).count()
    # Channel unread in one query (join instead of N+1)
    channel_unread = db.session.query(Message.id).join(
        ChannelParticipant,
        and_(
            Message.channel_id == ChannelParticipant.channel_id,
            ChannelParticipant.employee_id == my_id,
        )
    ).filter(
        Message.sender_id != my_id,
        or_(
            ChannelParticipant.last_read_at == None,
            Message.timestamp > ChannelParticipant.last_read_at,
        )
    ).count()
    return jsonify({'count': count + channel_unread})


@messages_bp.route('/react/<int:message_id>', methods=['POST'])
@login_required
def toggle_reaction(message_id):
    data = request.get_json()
    emoji = data.get('emoji')

    existing = MessageReaction.query.filter_by(
        message_id=message_id,
        employee_id=current_user.employee_id,
        emoji=emoji,
    ).first()

    if existing:
        db.session.delete(existing)
    else:
        db.session.add(MessageReaction(
            message_id=message_id,
            employee_id=current_user.employee_id,
            emoji=emoji,
        ))
    db.session.commit()
    return jsonify({'status': 'success'})