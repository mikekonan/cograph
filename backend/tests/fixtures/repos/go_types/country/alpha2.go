package country

import (
	"database/sql/driver"

	"github.com/mikekonan/go-types/v2/internal/utils"
)

type Alpha2Code string

func (code *Alpha2Code) UnmarshalJSON(data []byte) error {
	str, isEmptyValue, err := utils.UnsafeStringFromJson(data)
	if err != nil {
		return err
	}

	if isEmptyValue {
		return nil
	}

	country, err := ByAlpha2CodeErr(Alpha2Code(str))
	if err != nil {
		return err
	}

	*code = country.Alpha2Code()
	return nil
}

func (code Alpha2Code) Value() (value driver.Value, err error) {
	if code == "" {
		return "", nil
	}

	var country Country
	if country, err = ByAlpha2CodeErr(code); err != nil {
		return nil, err
	}

	return country.Alpha2Code().String(), nil
}

func (code Alpha2Code) Validate() (err error) {
	_, err = ByAlpha2CodeErr(code)
	return
}

func (code Alpha2Code) IsSet() bool {
	return len(string(code)) > 0
}

func (code Alpha2Code) String() string {
	return string(code)
}
