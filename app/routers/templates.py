from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ReplyTemplate
from app.schemas import SpinPreviewIn, SpinPreviewOut, TemplateIn, TemplateOut
from app.services import spintax

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _validate_body(body: str) -> None:
    try:
        spintax.validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _get_or_404(db: Session, template_id: int) -> ReplyTemplate:
    tpl = db.get(ReplyTemplate, template_id)
    if not tpl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    return tpl


@router.get("", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.scalars(select(ReplyTemplate).order_by(ReplyTemplate.name)).all()


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(data: TemplateIn, db: Session = Depends(get_db)):
    _validate_body(data.body)
    tpl = ReplyTemplate(**data.model_dump())
    db.add(tpl)
    db.commit()
    return tpl


@router.put("/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, data: TemplateIn, db: Session = Depends(get_db)):
    _validate_body(data.body)
    tpl = _get_or_404(db, template_id)
    tpl.name, tpl.body = data.name, data.body
    db.commit()
    return tpl


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, template_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/preview", response_model=SpinPreviewOut)
def preview(data: SpinPreviewIn):
    _validate_body(data.body)
    variables = {"author": "@author", "query": "#query", **data.variables}
    return SpinPreviewOut(
        variants=[spintax.spin(data.body, variables) for _ in range(data.count)],
        total_combinations=spintax.count_variants(data.body),
    )
