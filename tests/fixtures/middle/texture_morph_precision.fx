float4x4 WVP:WORLDVIEWPROJECTION;
float4 TC:TOONCOLOR;
float4 VS(float4 p:POSITION):POSITION {return mul(p,WVP);}
float4 PS():COLOR {float v=TC.COMPONENT*255;return float4(floor(v)/255,frac(v),0,1);}
technique Model < string MMDPass="object"; > {pass Main {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}
technique Shadow < string MMDPass="object_ss"; > {pass Main {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}}
