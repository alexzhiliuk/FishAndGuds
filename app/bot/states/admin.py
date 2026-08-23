from aiogram.fsm.state import State, StatesGroup


class MailingCreate(StatesGroup):
    name = State(); text = State(); image = State()


class MailingEdit(StatesGroup):
    name = State(); text = State(); image = State()


class MailingSchedule(StatesGroup):
    when = State()


class RestaurantLinkEdit(StatesGroup):
    value = State()


class RegistrationForm(StatesGroup):
    first_name = State()
    last_name = State()
    middle_name = State()
    birthday = State()
    email = State()
    consent = State()
    confirm = State()
