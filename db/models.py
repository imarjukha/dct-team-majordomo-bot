from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, String, func
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class BusinessUnit(Base):
    __tablename__ = "business_units"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)

    venues = relationship("Venue", back_populates="business_unit")
    employees = relationship("Employee", back_populates="business_unit")
    groups = relationship("Group", back_populates="business_unit")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=False)

    business_unit = relationship("BusinessUnit", back_populates="venues")
    employees = relationship("Employee", back_populates="venue")
    groups = relationship("Group", back_populates="venue")


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)

    employees = relationship("Employee", back_populates="role")
    groups = relationship("Group", back_populates="role")


class BotAdmin(Base):
    __tablename__ = "bot_admins"

    id = Column(Integer, primary_key=True)
    tg_user_id = Column(BigInteger, unique=True, nullable=False)
    tg_username = Column(String, nullable=True)
    is_superadmin = Column(Boolean, default=False)
    added_at = Column(DateTime, default=func.now())


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    tg_user_id = Column(BigInteger, unique=True, nullable=True)
    tg_username = Column(String, nullable=True)
    name = Column(String, nullable=True)

    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)

    status = Column(String, default="active")
    hired_at = Column(DateTime, default=func.now())
    fired_at = Column(DateTime, nullable=True)

    business_unit = relationship("BusinessUnit", back_populates="employees")
    venue = relationship("Venue", back_populates="employees")
    role = relationship("Role", back_populates="employees")
    memberships = relationship("GroupMember", back_populates="employee")


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    tg_chat_id = Column(BigInteger, unique=True, nullable=False)
    name = Column(String, nullable=False)

    business_unit_id = Column(Integer, ForeignKey("business_units.id"), nullable=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)

    is_configured = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    business_unit = relationship("BusinessUnit", back_populates="groups")
    venue = relationship("Venue", back_populates="groups")
    role = relationship("Role", back_populates="groups")
    memberships = relationship("GroupMember", back_populates="group")
    activity = relationship("ActivityLog", back_populates="group")


class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    joined_at = Column(DateTime, default=func.now())
    left_at = Column(DateTime, nullable=True)

    group = relationship("Group", back_populates="memberships")
    employee = relationship("Employee", back_populates="memberships")


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    date = Column(DateTime, default=func.now())
    message_count = Column(Integer, default=0)

    group = relationship("Group", back_populates="activity")


class AdminState(Base):
    """Stores pending input state for admin users (replaces context.user_data)."""
    __tablename__ = "admin_state"

    tg_user_id = Column(BigInteger, primary_key=True)
    action = Column(String, nullable=True)  # "bu" | "venue" | "role" | None
